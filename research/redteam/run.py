"""LLM red team: Claude writes attacks against our own guard and every query is kept.

Defensive testing (see research/redteam/README.md and prompt.md): a language model plays the
attacker against sqlguardllm over a synthetic schema; its output is executed only inside the guard
and on the disposable canary databases of tests/fuzz. Reuses the fuzzing oracle (canary data, two
worlds, real engines), so a canary in the output of an allowed query is a bypass.

    python research/redteam/run.py --preflight-only          # free token count and estimate
    python research/redteam/run.py --output-dir out          # one billable request, capped
    python research/redteam/run.py --rescore research/redteam/2026-11-27   # no API call

Cost is capped three ways: at most 50 attacks (``--max-attacks``), a hard output-token budget
(``--token-budget``, it is the request's ``max_tokens``) and a per-run USD ceiling (``--max-usd``)
that the worst-case estimate must stay under, or nothing is sent. The key is read from
``ANTHROPIC_API_KEY`` only, is never printed and is never part of a prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tests" / "fuzz"), str(ROOT / "tests")]

from fuzz_engines import ALL_ENGINES, Backend, open_backend  # noqa: E402
from fuzz_oracle import EngineStats, check_example, fuzz_guard  # noqa: E402
from fuzz_schema import COLUMNS, FUZZ_POLICY, HIDDEN, ROLES  # noqa: E402

from sqlguardllm import __version__  # noqa: E402
from sqlguardllm.audit import redact_sql  # noqa: E402

REDTEAM = Path(__file__).resolve().parent
PROMPT = REDTEAM / "prompt.md"
GALLERY = ROOT / "docs" / "ATTACK_GALLERY.md"
CORPUS = ROOT / "tests" / "corpus"

MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]
# One microdollar per USD per million tokens (USD 4 and USD 20 per million tokens, standard).
INPUT_PRICE = 4
OUTPUT_PRICE = 20
MAX_ATTACKS_CAP = 50
DEFAULT_TOKEN_BUDGET = 32_000
MAX_TOKEN_BUDGET = 64_000
DEFAULT_MAX_USD = 2.00
MAX_USD_CAP = 5.00  # the owner's total authorization for the first runs
DIALECTS = ("postgres", "mysql", "tsql", "sqlite", "duckdb")
LEAK_INVARIANTS = frozenset({"foreign_rows", "hidden_or_masked", "non_interference", "max_rows"})
SCHEMA_VERSION = 1

ATTACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "attacks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "dialect": {"type": "string", "enum": list(DIALECTS)},
                    "technique": {"type": "string"},
                    "target_invariant": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["sql", "dialect", "technique", "target_invariant", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["attacks"],
    "additionalProperties": False,
}
FIELDS = ("sql", "dialect", "technique", "target_invariant", "rationale")


class RedTeamError(Exception):
    """A failure that can be reported without printing request data or the key."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def system_prompt() -> str:
    return PROMPT.read_text(encoding="utf-8")


def policy_description() -> dict[str, Any]:
    return {
        "policy": FUZZ_POLICY,
        "hidden_columns": sorted(HIDDEN),
        "roles": ROLES,
        "note": "Branch 4 belongs to nobody. Results are capped at max_rows.",
    }


def policy_sha256() -> str:
    return sha256(json.dumps(policy_description(), sort_keys=True, separators=(",", ":")))


def corpus_categories() -> dict[str, int]:
    return {
        file.stem: len(yaml.safe_load(file.read_text(encoding="utf-8")) or [])
        for file in sorted(CORPUS.glob("*.yaml"))
    }


def user_message(max_attacks: int) -> str:
    """The request body: policy, schema, known attacks, count. Deterministic for one commit."""
    schema = {table: [f"{name} {kind}" for name, kind in cols] for table, cols in COLUMNS.items()}
    parts = [
        f"Write at most {max_attacks} attacks.",
        "## Policy, roles and masking\n" + json.dumps(policy_description(), indent=1),
        "## Schema (SQL types are portable int and text)\n" + json.dumps(schema, indent=1),
        "## Known attacks (docs/ATTACK_GALLERY.md)\n" + GALLERY.read_text(encoding="utf-8"),
        "## Corpus categories (cases per file in tests/corpus)\n"
        + json.dumps(corpus_categories(), indent=1),
        "## Dialects\n" + ", ".join(DIALECTS),
    ]
    return "\n\n".join(parts)


# -- cost ---------------------------------------------------------------------------------------


def cost_microusd(input_tokens: int, output_tokens: int) -> int:
    return input_tokens * INPUT_PRICE + output_tokens * OUTPUT_PRICE


def usd(microusd: int) -> str:
    return f"USD {microusd / 1_000_000:.4f}"


def estimate(counted: int, request_bytes: int, max_output: int) -> dict[str, int]:
    """Worst-case cost: the counted input with headroom, plus every output token allowed.

    Counting is an estimate, so reserve 25% (at least 1,024 tokens) or the byte size of the
    request, whichever is larger (as scripts/claude_issue_helper.py does). Thinking tokens are
    output tokens, so ``max_output`` (the request's ``max_tokens``) bounds them too.
    """
    reserve = max(counted + max(1_024, (counted + 3) // 4), request_bytes + 4_096)
    return {
        "counted_input_tokens": counted,
        "reserved_input_tokens": reserve,
        "max_output_tokens": max_output,
        "estimated_cost_microusd": cost_microusd(reserve, max_output),
    }


# -- judging ------------------------------------------------------------------------------------


def fingerprint(sql: str, dialect: str) -> str:
    """sha256 of the normalised SQL (literals replaced by ``?``, comments and spacing dropped)."""
    normalised = redact_sql(sql, dialect)
    if normalised is None:
        normalised = " ".join(sql.split())
    return sha256(f"{dialect}\n{normalised}")


def verdict(sql: str, dialect: str, role: str) -> dict[str, Any]:
    try:
        result = fuzz_guard(dialect).check(sql, ROLES[role])
    except Exception as exc:  # recorded, and the oracle reports it as fail_closed
        return {"allowed": None, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "allowed": result.ok,
        "violations": [
            {"code": v.code.value, "message": v.message, "detail": v.detail}
            for v in result.violations
        ],
        "rewritten_sql": result.sql,
        "applied_filters": list(result.applied_filters),
    }


def judge(sql: str, dialect: str, backends: Mapping[str, Backend | None]) -> dict[str, Any]:
    """Verdict per role plus the oracle's outcome per engine and role."""
    verdicts = {role: verdict(sql, dialect, role) for role in ROLES}
    engines: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for engine in ALL_ENGINES:
        if engine != dialect:
            engines[engine] = {"outcome": "not_applicable"}
            continue
        backend = backends.get(engine)
        per_role: dict[str, Any] = {}
        for role, context in ROLES.items():
            stats = EngineStats()
            found = check_example(sql, dialect, context, backend, stats, role=role)
            if stats.rejected:
                outcome = "rejected"
            elif backend is None:
                outcome = "guard_only"
            elif stats.engine_errors:
                outcome = "engine_error"
            elif stats.executed:
                outcome = "executed"
            else:
                outcome = "not_executed"
            items = [{"invariant": f.invariant, "detail": f.detail[:400]} for f in found]
            per_role[role] = {"outcome": outcome, "failures": items}
            failures.extend({**item, "role": role, "engine": engine} for item in items)
        engines[engine] = per_role
    if any(f["invariant"] in LEAK_INVARIANTS for f in failures):
        status = "bypass"
    elif failures:
        status = "invariant_failure"
    else:
        status = "allowed" if verdicts["north"].get("allowed") else "rejected"
    return {"verdict": verdicts["north"], "verdicts": verdicts, "engines": engines,
            "failures": failures, "status": status}  # fmt: skip


def open_backends(names: tuple[str, ...] = ALL_ENGINES) -> dict[str, Backend | None]:
    return {name: open_backend(name) for name in names}


def close_backends(backends: Mapping[str, Backend | None]) -> None:
    for backend in backends.values():
        if backend is not None:
            backend.close()


# -- model output -------------------------------------------------------------------------------


def parse_attacks(text: str) -> tuple[list[dict[str, str]], int]:
    """The valid attacks of the model's JSON, and how many items were malformed (kept out)."""
    try:
        data = json.loads(text)
    except ValueError:
        return [], 0
    items = data.get("attacks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], 0
    good: list[dict[str, str]] = []
    bad = 0
    for item in items:
        if (
            isinstance(item, dict)
            and all(isinstance(item.get(name), str) for name in FIELDS)
            and item["dialect"] in DIALECTS
            and item["sql"].strip()
        ):
            good.append({name: item[name] for name in FIELDS})
        else:
            bad += 1
    return good, bad


def git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def build_records(
    attacks: list[dict[str, str]], meta: dict[str, Any], backends: Mapping[str, Backend | None]
) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    records = []
    for index, attack in enumerate(attacks):
        fp = fingerprint(attack["sql"], attack["dialect"])
        first = seen.setdefault(fp, index)
        record = {
            "index": index,
            **meta,
            **attack,
            "fingerprint": fp,
            "duplicate_of": first if first != index else None,
            **judge(attack["sql"], attack["dialect"], backends),
            "fix_release": None,
        }
        records.append(record)
    return records


def totals(records: list[dict[str, Any]], malformed: int) -> dict[str, Any]:
    by_status = Counter(r["status"] for r in records)
    return {
        "generated": len(records),
        "unique": len({r["fingerprint"] for r in records}),
        "duplicates": sum(r["duplicate_of"] is not None for r in records),
        "allowed": by_status["allowed"],
        "rejected": by_status["rejected"],
        "bypasses": by_status["bypass"],
        "invariant_failures": by_status["invariant_failure"],
        "malformed_items": malformed,
        "by_technique": dict(
            Counter(r["technique"].strip().lower() for r in records).most_common()
        ),
        "by_dialect": dict(Counter(r["dialect"] for r in records)),
        "by_violation_code": dict(
            Counter(v["code"] for r in records for v in r["verdict"].get("violations", []))
        ),
    }


def bypass_cases(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Corpus cases (tests/corpus format) for every bypass, pending until triaged."""
    cases = []
    for r in records:
        if r["status"] != "bypass" or r["duplicate_of"] is not None:
            continue
        first = next(f for f in r["failures"] if f["invariant"] in LEAK_INVARIANTS)
        cases.append(
            {
                "id": f"redteam-{r['dialect']}-{r['fingerprint'][:8]}",
                "dialect": r["dialect"],
                "sql": r["sql"],
                "expect": "reject",
                "context": ROLES[first["role"]],
                "policy": FUZZ_POLICY,
                "pending": (
                    f"found by the LLM red team ({first['invariant']} on {first['engine']}); "
                    "triage: set expect and codes (or the safe rewrite), remove pending"
                ),
                "note": f"{r['technique']} | {first['detail'][:300]}",
            }
        )
    return cases


def write_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    path.write_text(
        yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )


def write_dataset(out: Path, records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
    (out / "attacks.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    cases = bypass_cases(records)
    if cases:
        write_cases(out / "redteam_cases.yaml", cases)


# -- generation ---------------------------------------------------------------------------------


def report_bypasses(records: list[dict[str, Any]]) -> None:
    bypasses = [r for r in records if r["status"] == "bypass"]
    if bypasses:
        print(f"BYPASSES FOUND: {len(bypasses)}")
        for r in bypasses:
            print(f"  [{r['dialect']}] {r['technique']}: {r['sql']}")
            for f in r["failures"]:
                print(f"    {f['invariant']} ({f['engine']}, {f['role']}): {f['detail']}")
    else:
        print("No bypass found.")


def generate(
    client: Any, args: argparse.Namespace, backends: Mapping[str, Backend | None], today: str
) -> int:
    system = system_prompt()
    message = user_message(args.max_attacks)
    request_bytes = len((system + message).encode("utf-8"))
    counted = client.messages.count_tokens(
        model=MODEL, system=system, messages=[{"role": "user", "content": message}]
    ).input_tokens
    est = estimate(counted, request_bytes, args.token_budget)
    ceiling = round(args.max_usd * 1_000_000)
    print(
        f"Estimated worst-case cost {usd(est['estimated_cost_microusd'])} "
        f"({est['reserved_input_tokens']} input + {est['max_output_tokens']} output tokens), "
        f"ceiling {usd(ceiling)}."
    )
    meta = {
        "model": MODEL,
        "prompt_sha256": sha256(system),
        "policy_sha256": policy_sha256(),
        "sqlguardllm_version": __version__,
        "git_sha": git_sha(),
    }
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": today,
        "status": "pending",
        "bypasses": [],
        **meta,
        "request_sha256": sha256(message),
        "max_attacks": args.max_attacks,
        "token_budget": args.token_budget,
        "max_usd": args.max_usd,
        "estimate": est,
        "engines_executed": sorted(n for n, b in backends.items() if b is not None),
    }
    out = args.output_dir / today
    if est["estimated_cost_microusd"] > ceiling:
        manifest.update(status="refused_by_budget")
        write_dataset(out, [], manifest)
        raise RedTeamError(
            f"The estimate {usd(est['estimated_cost_microusd'])} exceeds the ceiling "
            f"{usd(ceiling)}: nothing was sent."
        )
    if args.preflight_only:
        manifest.update(status="preflight")
        write_dataset(out, [], manifest)
        return 0

    # Written before the only billable call, so an ambiguous failure never leaves no trace.
    manifest.update(status="generation_attempted")
    write_dataset(out, [], manifest)
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=args.token_budget,
        system=system,
        messages=[{"role": "user", "content": message}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": ATTACK_SCHEMA},
        },
        betas=BETAS,
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()

    usage = response.usage
    input_tokens = int(usage.input_tokens or 0)
    output_tokens = int(usage.output_tokens or 0)
    actual = cost_microusd(input_tokens, output_tokens)
    manifest.update(
        model_reported=response.model,
        stop_reason=response.stop_reason,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        cost_microusd=actual,
        cost_usd=round(actual / 1_000_000, 6),
        fallback_used=any(b.type == "fallback" for b in response.content),
        over_estimate=input_tokens > est["reserved_input_tokens"]
        or output_tokens > args.token_budget,
    )
    meta["model"] = response.model  # the model that served the request, not the one asked for
    records: list[dict[str, Any]] = []
    malformed = 0
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        manifest.update(status="refused", refusal_category=getattr(details, "category", None))
        code = 0
    else:
        text = "".join(b.text for b in response.content if b.type == "text")
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key and key in text:
            raise RedTeamError("The response contains the API key; discarded.")
        attacks, malformed = parse_attacks(text)
        manifest["over_max_attacks"] = max(0, len(attacks) - args.max_attacks)
        if not attacks:
            manifest.update(status="malformed", malformed_items=malformed)
            code = 1
        else:
            records = build_records(attacks, meta, backends)
            manifest["status"] = "complete"
            code = 0
    manifest.update(totals(records, malformed))
    manifest["bypasses"] = [
        {"index": r["index"], "dialect": r["dialect"], "technique": r["technique"], "sql": r["sql"]}
        for r in records
        if r["status"] == "bypass"
    ]
    write_dataset(out, records, manifest)
    report_bypasses(records)
    print(
        f"Status {manifest['status']}; {len(records)} attacks kept in {out}; "
        f"cost {usd(actual)} ({input_tokens} in, {output_tokens} out)."
    )
    return 1 if manifest["bypasses"] else code


# -- rescore ------------------------------------------------------------------------------------


def rescore(directory: Path, backends: Mapping[str, Backend | None]) -> dict[str, Any]:
    """Judge a stored dataset again with the current guard and oracle. No API call."""
    stored = [
        json.loads(line)
        for line in (directory / "attacks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records, changed = [], []
    for old in stored:
        new = judge(old["sql"], old["dialect"], backends)
        records.append({**old, **new, "index": old["index"], "duplicate_of": old["duplicate_of"]})
        was, now = old["verdict"].get("allowed"), new["verdict"].get("allowed")
        if was != now:
            changed.append({"index": old["index"], "was_allowed": was, "now_allowed": now})
    summary = totals(records, 0)
    return {
        "sqlguardllm_version": __version__,
        "git_sha": git_sha(),
        "policy_sha256": policy_sha256(),
        "dataset": directory.name,
        "bypasses": [r["index"] for r in records if r["status"] == "bypass"],
        "invariant_failures": [r["index"] for r in records if r["status"] == "invariant_failure"],
        "changed_verdicts": changed,
        "engines_executed": sorted(n for n, b in backends.items() if b is not None),
        "totals": summary,
    }


# -- command line -------------------------------------------------------------------------------


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", type=Path, default=REDTEAM, help="Parent of <date>/.")
    parser.add_argument("--date", help="Dataset directory name (default: today, UTC).")
    parser.add_argument("--max-attacks", type=int, default=MAX_ATTACKS_CAP)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD)
    parser.add_argument(
        "--preflight-only", action="store_true", help="Count tokens, no generation."
    )
    parser.add_argument("--rescore", type=Path, metavar="DIR", help="Re-judge a stored dataset.")
    return parser


def main(argv: list[str] | None = None, client: Any = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_attacks <= MAX_ATTACKS_CAP:
        parser.error(f"--max-attacks must be 1 to {MAX_ATTACKS_CAP}")
    if not 1_024 <= args.token_budget <= MAX_TOKEN_BUDGET:
        parser.error(f"--token-budget must be 1024 to {MAX_TOKEN_BUDGET}")
    if not 0 < args.max_usd <= MAX_USD_CAP:
        parser.error(f"--max-usd must be above 0 and at most {MAX_USD_CAP}")
    today = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"[0-9A-Za-z_.-]{1,40}", today):
        parser.error("--date must be a plain directory name")

    if args.rescore:
        backends = open_backends()
        try:
            summary = rescore(args.rescore, backends)
        finally:
            close_backends(backends)
        (args.rescore / "rescore.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in summary.items() if k != "totals"}, indent=2))
        return 1 if summary["bypasses"] or summary["invariant_failures"] else 0

    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            print("Red team: set ANTHROPIC_API_KEY to contact Claude.", file=sys.stderr)
            return 1
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(max_retries=2)
    backends = open_backends() if not args.preflight_only else {}
    try:
        return generate(client, args, backends, today)
    except RedTeamError as exc:
        print(f"Red team: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # an API failure: report the type only, never request data
        print(f"Red team: the Claude request failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    finally:
        close_backends(backends)


if __name__ == "__main__":
    sys.exit(main())
