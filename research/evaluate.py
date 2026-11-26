"""Reproduce execution, regression and warm validation measurements.

Run from the repository root. Optional database packages are isolated from the
library's runtime dependencies. Nothing is sent to an LLM or an external DB.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import yaml
from workload import cases, fixture_sql, manifest

from sqlguardllm import Guard, Policy

ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS = 8
BLOCK = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|copy|attach|pragma|exec)\b", re.I
)


def policy(dialect: str, *, filters: bool = True) -> Policy:
    return Policy(
        dialect=dialect,
        tables={"invoices", "customers", "branches"},
        hidden_columns={"customers.email"},
        row_filters={
            t: "tenant_id = {user.tenant_id}" for t in ("invoices", "customers", "branches")
        }
        if filters
        else {},
        max_rows=MAX_ROWS,
    )


def canonical(rows: list) -> list[str]:
    return sorted(json.dumps(list(row), default=str, ensure_ascii=False) for row in rows)


class LocalBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.temp = tempfile.TemporaryDirectory(prefix="sqlguard-study-")
        self.connections = {}
        for key, tenant in (("full", None), ("tenant_1", 1), ("tenant_2", 2)):
            schema = fixture_sql(tenant, oracle=tenant is not None)
            if name == "sqlite":
                conn = sqlite3.connect(":memory:")
                conn.executescript(schema)
                conn.execute("PRAGMA query_only = ON")
                self.engine_version = sqlite3.sqlite_version
            else:
                import duckdb  # noqa: PLC0415

                path = str(Path(self.temp.name) / f"{key}.duckdb")
                conn = duckdb.connect(path)
                conn.execute(schema)
                conn.close()
                conn = duckdb.connect(path, read_only=True)
                self.engine_version = duckdb.__version__
            self.connections[key] = conn

    def query(self, key: str, sql: str, *, role: str | None = None) -> list:
        return self.connections[key].execute(sql).fetchmany(MAX_ROWS)

    def close(self) -> None:
        for conn in self.connections.values():
            conn.close()
        self.temp.cleanup()


class PGliteBackend:
    """PostgreSQL/WASM, explicitly not a native PostgreSQL server measurement."""

    name = "postgres_pglite"

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["node", str(ROOT / "research/pglite.mjs")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        result = self.request(
            {
                "op": "init",
                "fixtures": {
                    "full": fixture_sql(),
                    "tenant_1": fixture_sql(1, oracle=True),
                    "tenant_2": fixture_sql(2, oracle=True),
                },
            }
        )
        self.engine_version = result["version"]

    def request(self, request: dict) -> dict:
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("PGlite worker stopped before responding")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def query(self, key: str, sql: str, *, role: str | None = None) -> list:
        return self.request({"op": "query", "key": key, "sql": sql, "role": role})["rows"][
            :MAX_ROWS
        ]

    def close(self) -> None:
        self.request({"op": "close"})
        self.proc.wait(timeout=20)


def percentile(values: list[int], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))] / 1e6


def run_backend(backend, rounds: int, warmup: int) -> tuple[list, list]:
    dialect = "postgres" if backend.name == "postgres_pglite" else backend.name
    full = Guard(policy(dialect))
    ablated = Guard(policy(dialect, filters=False))
    shape = Guard(Policy(dialect=dialect, max_rows=MAX_ROWS))
    records, latencies = [], []
    for tenant in (1, 2):
        context = {"user": {"tenant_id": tenant}}
        for case in cases(tenant):
            methods = {
                "keyword_filter": lambda q: (not bool(BLOCK.search(q)), q, []),
                "read_only_shape": functools.partial(check, shape, context=context),
                "no_row_filters": functools.partial(check, ablated, context=context),
                "sqlguardllm": functools.partial(check, full, context=context),
            }
            expected_rows = None
            if case.expected == "allow":
                # A broken oracle is fatal, not silently counted as a blocked attack.
                expected_rows = canonical(backend.query(f"tenant_{tenant}", case.sql))
            for name, fn in methods.items():
                for _ in range(warmup):
                    fn(case.sql)
                samples = []
                for _ in range(rounds):
                    started = time.perf_counter_ns()
                    decision = fn(case.sql)
                    samples.append(time.perf_counter_ns() - started)
                ok, rewritten, codes = decision
                row = {
                    "engine": backend.name,
                    "tenant": tenant,
                    **asdict(case),
                    "method": name,
                    "expected": case.expected,
                    "ok": ok,
                    "sql_out": rewritten if ok else None,
                    "codes": codes,
                }
                if not ok:
                    row["outcome"] = "blocked" if case.expected == "reject" else "over_rejected"
                else:
                    try:
                        actual = backend.query("full", rewritten)
                        row["rows"] = actual
                        row["outcome"] = (
                            "policy_violation"
                            if case.expected == "reject" or canonical(actual) != expected_rows
                            else "authorized_match"
                        )
                    except Exception as exc:
                        row["outcome"] = "execution_error"
                        row["error"] = str(exc).splitlines()[0][:200]
                records.append(row)
                latencies.append(
                    {
                        "engine": backend.name,
                        "tenant": tenant,
                        "case": case.id,
                        "method": name,
                        "samples_ns": samples,
                        "p50_ms": statistics.median(samples) / 1e6,
                        "p95_ms": percentile(samples, 0.95),
                    }
                )
            if backend.name == "postgres_pglite":
                row = {
                    "engine": backend.name,
                    "tenant": tenant,
                    **asdict(case),
                    "method": "native_rls_columns",
                    "expected": case.expected,
                    "ok": True,
                    "sql_out": case.sql,
                }
                try:
                    actual = backend.query("full", case.sql, role=f"study_tenant_{tenant}")
                    row["rows"] = actual
                    row["outcome"] = (
                        "policy_violation"
                        if case.expected == "reject" or canonical(actual) != expected_rows
                        else "authorized_match"
                    )
                except Exception as exc:
                    row["outcome"] = "blocked" if case.expected == "reject" else "execution_error"
                    row["error"] = str(exc).splitlines()[0][:200]
                records.append(row)
    return records, latencies


def check(guard: Guard, sql: str, context: dict) -> tuple:
    result = guard.check(sql, context)
    return result.ok, result.sql, sorted(code.value for code in result.codes)


def regression() -> list[dict]:
    # This is development-regression evidence, not an independent generalization test.
    tables = {"invoices", "invoice_lines", "branches", "customers", "products"}
    results = []
    for file in sorted((ROOT / "tests/corpus").glob("*.yaml")):
        for case in yaml.safe_load(file.read_text()) or []:
            if case.get("pending"):
                results.append(
                    {
                        "id": case["id"],
                        "dialect": case["dialect"],
                        "outcome": "skipped",
                        "reason": case["pending"],
                    }
                )
                continue
            options = {
                "dialect": case["dialect"],
                "tables": tables,
                "hidden_columns": {"customers.email", "customers.tax_id"},
                "row_filters": {"invoices": "branch_id IN ({user.branches})"},
                "max_rows": 1000,
                "default_schema": "dbo" if case["dialect"] == "tsql" else None,
            }
            options.update(case.get("policy", {}))
            r = Guard(Policy(**options)).check(
                case["sql"], case.get("context", {"user": {"branches": [1, 2]}})
            )
            success = (r.ok == (case["expect"] == "allow")) and set(case.get("codes", [])) <= {
                c.value for c in r.codes
            }
            if r.ok:
                success = (
                    success
                    and all(x in r.sql for x in case.get("contains", []))
                    and all(x not in r.sql for x in case.get("not_contains", []))
                )
            results.append(
                {
                    "id": case["id"],
                    "dialect": case["dialect"],
                    "expected": case["expect"],
                    "outcome": "pass" if success else "fail",
                    "codes": sorted(c.value for c in r.codes),
                }
            )
    return results


def summarize(records: list[dict], timing: list[dict]) -> list[dict]:
    summary = []
    keys = sorted({(r["engine"], r["method"]) for r in records})
    for engine, method in keys:
        selected = [r for r in records if (r["engine"], r["method"]) == (engine, method)]
        samples = [
            n
            for r in timing
            if (r["engine"], r["method"]) == (engine, method)
            for n in r["samples_ns"]
        ]
        summary.append(
            {
                "engine": engine,
                "method": method,
                "n": len(selected),
                **dict(Counter(r["outcome"] for r in selected)),
                "validation_p50_ms": statistics.median(samples) / 1e6 if samples else None,
                "validation_p95_ms": percentile(samples, 0.95) if samples else None,
            }
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default="sqlite,duckdb,postgres_pglite")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--out", type=Path, default=ROOT / "research/results")
    args = parser.parse_args()
    if args.rounds < 1 or args.warmup < 0:
        parser.error("rounds must be positive and warmup nonnegative")
    args.out.mkdir(parents=True, exist_ok=True)
    all_records, all_timing, engines = [], [], {}
    for name in args.engines.split(","):
        print(f"Evaluating {name}...", flush=True)
        backend = PGliteBackend() if name == "postgres_pglite" else LocalBackend(name)
        try:
            records, timing = run_backend(backend, args.rounds, args.warmup)
            engines[name] = backend.engine_version
            all_records.extend(records)
            all_timing.extend(timing)
        finally:
            backend.close()
    corpus = regression()
    sources = [ROOT / "research/workload.py", ROOT / "research/evaluate.py"]
    package_files = sorted((ROOT / "src/sqlguardllm").rglob("*.py"))
    digest = hashlib.sha256()
    for file in package_files:
        digest.update(str(file.relative_to(ROOT)).encode())
        digest.update(file.read_bytes())
    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "engine_source_sha256": digest.hexdigest(),
        "workload_sha256": hashlib.sha256(sources[0].read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "engines": engines,
        "packages": {
            p: version(p)
            for p in (
                "sqlguardllm",
                "sqlglot",
                "PyYAML",
                *(("duckdb",) if "duckdb" in engines else ()),
            )
        },
        "rounds": args.rounds,
        "warmup": args.warmup,
        "row_cap": MAX_ROWS,
        "workload_scope": (
            "48 authored SQL templates x 2 tenant contexts; not an independently held-out benchmark"
        ),
        "timing_scope": (
            "warm Python validation only; excludes DB, process startup, network, policy compilation"
        ),
    }
    artifacts = {
        "metadata.json": metadata,
        "workload.json": manifest(),
        "execution.json": all_records,
        "latency.json": all_timing,
        "regression.json": corpus,
        "summary.json": summarize(all_records, all_timing),
    }
    for name, data in artifacts.items():
        # Keep all observations and timing samples, with one record per line
        # inside a valid JSON array so the public diff stays reviewable.
        if name in {"execution.json", "latency.json"}:
            output = (
                "[\n"
                + ",\n".join(
                    json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":"))
                    for row in data
                )
                + "\n]\n"
            )
        else:
            output = json.dumps(data, indent=2, default=str, ensure_ascii=False) + "\n"
        (args.out / name).write_text(output)
    print(json.dumps(artifacts["summary.json"], indent=2))
    failures = [
        r
        for r in all_records
        if r["method"] == "sqlguardllm"
        and r["outcome"] in {"execution_error", "policy_violation", "over_rejected"}
    ]
    return 1 if failures or any(r["outcome"] == "fail" for r in corpus) else 0


if __name__ == "__main__":
    raise SystemExit(main())
