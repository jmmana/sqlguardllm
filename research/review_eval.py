"""Review-driven ablations, edge probes, grammar fuzzing and mutation testing.

Results are descriptive and preserve errors. No deliberately broken guard is
installed or written to production source files. Forbidden file/function probes
are admission-only: they never reach an executor, even under a mutant.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sqlglot
from evaluate import BLOCK, MAX_ROWS, LocalBackend, PGliteBackend, canonical, policy
from expanded_cases import Probe, edge_cases, fixtures, generated, original
from native_postgres import NativeBackend
from postgres_service import PostgresBackend
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

import sqlguardllm.guard as guard_module
from sqlguardllm import Guard

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 2026112601
METHODS = ("keyword_filter", "read_only_shape", "no_row_filters", "outer_where", "sqlguardllm")


def guard_for(dialect: str, *, filters=True, view=False) -> Guard:
    configured = replace(
        policy(dialect, filters=filters),
        default_schema="public" if dialect == "postgres" else "main",
    )
    if view:
        configured = replace(
            configured,
            tables=set(configured.tables) | {"invoice_view"},
            row_filters={
                **configured.row_filters,
                **({"invoice_view": "tenant_id = {user.tenant_id}"} if filters else {}),
            },
        )
    return Guard(configured)


def guarded(guard: Guard, sql: str, context: dict) -> tuple:
    r = guard.check(sql, context)
    return r.ok, r.sql, sorted(c.value for c in r.codes)


def outer_where(guard: Guard, dialect: str, sql: str, context: dict) -> tuple:
    """Only filter physical sources of the outer SELECT; preserve Boolean AND.

    It keeps all ordinary checks but cannot protect reads inside CTEs, scalar
    subqueries or set branches, and moves LEFT JOIN restrictions into WHERE.
    This prototype re-parses the admitted SQL; its cost is not an optimized
    implementation of outer-WHERE rewriting.
    """
    ok, rewritten, codes = guarded(guard, sql, context)
    if not ok:
        return ok, rewritten, codes
    root = sqlglot.parse_one(rewritten, read=dialect)
    scopes = list(traverse_scope(root))
    scope = next((s for s in scopes if s.expression is root), None)
    predicates = []
    if isinstance(root, exp.Select) and scope is not None:
        for alias, source in scope.sources.items():
            if isinstance(source, exp.Table) and source.name.lower() in guard.policy.tables:
                predicates.append(
                    exp.EQ(
                        this=exp.column("tenant_id", table=alias),
                        expression=exp.convert(context["user"]["tenant_id"]),
                    )
                )
        if predicates:
            root = root.where(*predicates)
    return True, root.sql(dialect=dialect), []


def methods(dialect: str, context: dict, view=False) -> dict:
    full = guard_for(dialect, view=view)
    no_rows = guard_for(dialect, filters=False, view=view)
    shape = Guard(replace(policy(dialect, filters=False), tables=None, hidden_columns=frozenset()))
    return {
        "keyword_filter": lambda q: (not bool(BLOCK.search(q)), q, []),
        "read_only_shape": functools.partial(guarded, shape, context=context),
        "no_row_filters": functools.partial(guarded, no_rows, context=context),
        "outer_where": functools.partial(outer_where, no_rows, dialect, context=context),
        "sqlguardllm": functools.partial(guarded, full, context=context),
    }


def observe(backend, tenant: int, probe: Probe, method: str, decision, *, native_role=None) -> dict:
    row = {"engine": backend.name, "tenant": tenant, **asdict(probe), "method": method}
    try:
        ok, rewritten, codes = decision(probe.sql)
    except Exception as exc:
        return row | {"ok": False, "outcome": "guard_error", "error": str(exc)[:200]}
    row.update(ok=ok, sql_out=rewritten if ok else None, codes=codes)
    if not ok:
        return row | {"outcome": "blocked" if probe.expected == "reject" else "over_rejected"}
    if probe.expected == "reject" and not probe.execute:
        return row | {"outcome": "unsafe_admission", "executed": False}
    expected = None
    if probe.expected == "allow":
        try:
            expected = canonical(backend.query(f"tenant_{tenant}", probe.sql, fetch_limit=MAX_ROWS))
        except Exception as exc:
            return row | {"outcome": "oracle_error", "error": str(exc)[:200]}
    try:
        rows = backend.query("full", rewritten, role=native_role, fetch_limit=MAX_ROWS + 1)
        if native_role:
            rows = rows[:MAX_ROWS]  # RLS grants do not implement a SQL row cap.
        row.update(rows=rows, executed=True)
    except Exception as exc:
        return row | {
            "outcome": "blocked"
            if native_role and probe.expected == "reject"
            else "execution_error",
            "error": str(exc)[:200],
        }
    if probe.expected == "reject":
        outcome = "unsafe_admission"
    elif len(rows) > MAX_ROWS:
        outcome = "row_cap_violation"
    elif canonical(rows) != expected:
        outcome = "result_mismatch"
    else:
        outcome = "authorized_match"
    return row | {"outcome": outcome}


def outcome_counts(rows: list[dict]) -> list[dict]:
    keys = sorted({(r["engine"], r["method"], r["kind"]) for r in rows})
    return [
        {
            "engine": e,
            "method": m,
            "kind": k,
            "n": len(selected),
            **dict(Counter(r["outcome"] for r in selected)),
        }
        for e, m, k in keys
        if (selected := [r for r in rows if (r["engine"], r["method"], r["kind"]) == (e, m, k)])
    ]


def mutate(name: str):
    if name == "remove_row_filters":
        return patch.object(guard_module, "apply_filters", lambda refs, compiled, context: ([], []))
    if name == "remove_hidden_check":
        return patch.object(guard_module, "check_columns", lambda scopes, compiled: [])
    if name == "remove_star_check":
        function = guard_module.check_columns
        return patch.object(
            guard_module,
            "check_columns",
            lambda scopes, compiled: [v for v in function(scopes, compiled) if "*" not in v.detail],
        )
    if name == "remove_function_check":
        return patch.object(guard_module, "check_functions", lambda root, compiled: [])
    if name == "remove_limit":
        return patch.object(guard_module, "apply_limit", lambda root, maximum: root)
    if name == "skip_cte_sources":
        function = guard_module.collect_sources

        def skipped(root, scopes, compiled):
            refs, problems = function(root, scopes, compiled)
            return [ref for ref in refs if ref.table.find_ancestor(exp.CTE) is None], problems

        return patch.object(guard_module, "collect_sources", skipped)
    if name == "filter_first_occurrence":
        function = guard_module.apply_filters
        return patch.object(
            guard_module,
            "apply_filters",
            lambda refs, compiled, context: function(refs[:1], compiled, context),
        )
    raise ValueError(name)


MUTANTS = (
    "remove_row_filters",
    "remove_hidden_check",
    "remove_star_check",
    "remove_function_check",
    "remove_limit",
    "skip_cte_sources",
    "filter_first_occurrence",
)


def mutation_study(backend, fuzz_n: int, seed: int) -> tuple[list, list]:
    sets = {
        "original_48": [(t, p) for t in (1, 2) for p in original(t)],
        "review_edges": [(t, p) for t in (1, 2) for p in edge_cases("sqlite", t)],
        "generated": generated(fuzz_n, seed, "sqlite"),
    }
    results, failures = [], []
    # Credit a kill only when the unmutated control passes the same probe.
    controls = {}
    for suite, probes in sets.items():
        for tenant, probe in probes:
            decision = methods("sqlite", {"user": {"tenant_id": tenant}}, probe.allow_view)[
                "sqlguardllm"
            ]
            controls[(suite, tenant, probe.id)] = observe(
                backend, tenant, probe, "control", decision
            )["outcome"]
    for mutant in MUTANTS:
        with mutate(mutant):
            for suite, probes in sets.items():
                detected = []
                for tenant, probe in probes:
                    control = controls[(suite, tenant, probe.id)]
                    if control not in {"authorized_match", "blocked"}:
                        continue
                    decision = methods("sqlite", {"user": {"tenant_id": tenant}}, probe.allow_view)[
                        "sqlguardllm"
                    ]
                    row = observe(backend, tenant, probe, mutant, decision)
                    if row["outcome"] != control:
                        detected.append(probe.id)
                        failures.append(row | {"suite": suite, "control_outcome": control})
                results.append(
                    {
                        "mutant": mutant,
                        "suite": suite,
                        "killed": bool(detected),
                        "detecting_observations": len(detected),
                        "first_case": detected[0] if detected else None,
                    }
                )
        print("Mutation completed:", mutant, flush=True)
    return results, failures


def timing_study(rounds: int, seed: int) -> dict:
    before = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    pinned = None
    if before:
        try:
            os.sched_setaffinity(0, {before[0]})
            pinned = sorted(os.sched_getaffinity(0))
        except OSError:
            pass
    rng = random.Random(seed)
    items = [(tenant, probe) for tenant in (1, 2) for probe in original(tenant)]
    runners = {t: methods("postgres", {"user": {"tenant_id": t}}) for t in (1, 2)}
    for t in (1, 2):
        runners[t]["parse_only"] = functools.partial(sqlglot.parse, read="postgres")
    samples = defaultdict(list)
    for tenant, probe in items:
        for fn in runners[tenant].values():
            for _ in range(5):
                fn(probe.sql)
    events = [(tenant, probe, r) for r in range(rounds) for tenant, probe in items]
    rng.shuffle(events)
    for tenant, probe, _ in events:
        order = list(runners[tenant])
        rng.shuffle(order)
        for method in order:
            started = time.perf_counter_ns()
            runners[tenant][method](probe.sql)
            samples[(probe.id, probe.kind, method)].append(time.perf_counter_ns() - started)
    rows = [
        {
            "case": case,
            "kind": kind,
            "method": method,
            "ast_nodes": sum(
                1
                for _ in sqlglot.parse_one(
                    next(p.sql for _, p in items if p.id == case), read="postgres"
                ).walk()
            ),
            "samples_ns": values,
            "median_ms": statistics.median(values) / 1e6,
        }
        for (case, kind, method), values in sorted(samples.items())
    ]
    bootstrap = []
    for method in list(runners[1]):
        medians = [r["median_ms"] for r in rows if r["method"] == method]
        draws = sorted(statistics.median(rng.choices(medians, k=len(medians))) for _ in range(2000))
        bootstrap.append(
            {
                "method": method,
                "template_clusters": len(medians),
                "median_template_ms": statistics.median(medians),
                "ci95_low_ms": draws[49],
                "ci95_high_ms": draws[1949],
            }
        )
    return {
        "seed": seed,
        "rounds": rounds,
        "warmup": 5,
        "affinity_before": before,
        "affinity_measured": pinned,
        "order": "events and within-event methods shuffled with a fixed seed",
        "bootstrap_unit": "template (both contexts and all repetitions retained within each cluster)",
        "samples": rows,
        "bootstrap": bootstrap,
    }


def write(path: Path, value) -> None:
    if isinstance(value, list):
        output = (
            "[\n"
            + ",\n".join(
                json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":"))
                for row in value
            )
            + "\n]\n"
        )
    else:
        output = json.dumps(value, indent=2, default=str, ensure_ascii=False) + "\n"
    if path.name == "generated.json":
        path.with_suffix(".json.gz").write_bytes(gzip.compress(output.encode(), mtime=0))
    else:
        path.write_text(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default="sqlite,duckdb,postgres_pglite,postgres_native_single")
    parser.add_argument("--generated", type=int, default=5000)
    parser.add_argument("--mutation-generated", type=int, default=300)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=ROOT / "research/results-review")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    original_rows, edges, fuzz_rows, fuzz_summaries = [], [], [], []
    runtime_versions = {}
    for engine in args.engines.split(","):
        dialect = "postgres" if engine.startswith("postgres") else engine
        factory = (
            (lambda fixture, name=engine: LocalBackend(name, fixture))
            if not engine.startswith("postgres")
            else PostgresBackend
            if engine == "postgres_service"
            else PGliteBackend
            if engine == "postgres_pglite"
            else NativeBackend
        )
        print("Original cases:", engine, flush=True)
        backend = factory(fixtures())
        try:
            runtime_versions[engine] = backend.engine_version
            for tenant in (1, 2):
                runners = methods(dialect, {"user": {"tenant_id": tenant}})
                for probe in original(tenant):
                    for name, decision in runners.items():
                        original_rows.append(observe(backend, tenant, probe, name, decision))
                    if engine in {"postgres_pglite", "postgres_service"}:
                        original_rows.append(
                            observe(
                                backend,
                                tenant,
                                probe,
                                "native_rls_columns",
                                lambda sql: (True, sql, []),
                                native_role=f"study_tenant_{tenant}",
                            )
                        )
        finally:
            backend.close()
        print("Review probes and generator:", engine, flush=True)
        backend = factory(fixtures(null_extension=True))
        try:
            runners = {t: methods(dialect, {"user": {"tenant_id": t}}) for t in (1, 2)}
            for tenant in (1, 2):
                for probe in edge_cases(dialect, tenant):
                    decision = (
                        runners[tenant]["sqlguardllm"]
                        if not probe.allow_view
                        else methods(dialect, {"user": {"tenant_id": tenant}}, view=True)[
                            "sqlguardllm"
                        ]
                    )
                    edges.append(observe(backend, tenant, probe, "sqlguardllm", decision))
            generated_probes = generated(args.generated, args.seed, dialect)
            current = []
            for tenant, probe in generated_probes:
                current.append(
                    observe(backend, tenant, probe, "sqlguardllm", runners[tenant]["sqlguardllm"])
                )
            counts = Counter(r["outcome"] for r in current)
            violations = sum(
                counts[x] for x in ("unsafe_admission", "result_mismatch", "row_cap_violation")
            )
            fuzz_summaries.append(
                {
                    "engine": engine,
                    "draws": len(current),
                    "unique_sql": len({p.sql for _, p in generated_probes}),
                    "distinct_families": len({p.family for _, p in generated_probes}),
                    "seed": args.seed,
                    "sampling": "with replacement from the published authored grammar; correlated across runtimes",
                    "policy_violations": violations,
                    **dict(counts),
                    "conditional_zero_event_upper95": 1 - 0.05 ** (1 / len(current))
                    if not violations and current
                    else None,
                }
            )
            fuzz_rows.extend(current)
        finally:
            backend.close()
        write(args.out / "original.json", original_rows)
        write(args.out / "edges.json", edges)
        write(args.out / "generated.json", fuzz_rows)
        write(args.out / "generated-summary.json", fuzz_summaries)
    backend = LocalBackend("sqlite", fixtures(null_extension=True))
    try:
        mutations, failures = mutation_study(backend, args.mutation_generated, args.seed + 1)
    finally:
        backend.close()
    write(args.out / "mutations.json", mutations)
    write(args.out / "mutation-detections.json", failures)
    write(args.out / "summary.json", outcome_counts(original_rows))
    write(args.out / "timing.json", timing_study(args.rounds, args.seed + 2))
    sources = [
        Path(__file__),
        ROOT / "research/expanded_cases.py",
        ROOT / "research/native_postgres.py",
        ROOT / "research/postgres_service.py",
        ROOT / "research/evaluate.py",
        ROOT / "research/workload.py",
    ]
    write(
        args.out / "metadata.json",
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "runtime_versions": runtime_versions,
            "python": sys.version,
            "platform": platform.platform(),
            "seed": args.seed,
            "generator_draws_per_runtime": args.generated,
            "mutation_generated_draws": args.mutation_generated,
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sources
            },
            "engine_source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / "src/sqlguardllm").rglob("*.py"))
            },
            "oracle_fetch_cap": MAX_ROWS,
            "candidate_fetch_probe": MAX_ROWS + 1,
            "single_user_scope": "native engine/query planner only, not client-server concurrency or privilege-enforcement equivalence",
        },
    )
    print(
        json.dumps(
            {
                "generated": fuzz_summaries,
                "edge_outcomes": dict(Counter(r["outcome"] for r in edges)),
                "mutants": mutations,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
