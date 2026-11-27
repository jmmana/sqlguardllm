"""The five invariants checked on every fuzzing example, and the run statistics.

1. ``foreign_rows``: no canary of another role's rows in the output (and, through world B, no
   influence of rows no role may see: ``non_interference``).
2. ``hidden_or_masked``: no canary of a hidden or masked column in the output (and no influence
   of their values: ``non_interference``).
3. ``max_rows``: never more rows than ``max_rows``.
4. ``fail_closed``: ``Guard.check`` never raises; a rejection always has a reason.
5. ``idempotence``: the rewritten SQL is allowed again; rewriting it again changes nothing, or,
   when a row filter or a mask was applied (those are re-applied by design), returns the same rows.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from fuzz_engines import Backend, EngineError
from fuzz_schema import FUZZ_POLICY, MAX_ROWS, ROLES, Row, leaks
from sqlglot import exp
from sqlglot.errors import SqlglotError

from conftest import standard_policy
from sqlguardllm import Guard

INVARIANTS = (
    "foreign_rows",
    "hidden_or_masked",
    "non_interference",
    "max_rows",
    "fail_closed",
    "idempotence",
)
# Engines known to return more rows than the LIMIT of the SQL they run, in some plans.
# DuckDB 2.0.1: a LEFT JOIN whose right side statistics prove empty (`LEFT JOIN customers AS t ON
# t.name = 'a'`) loses the LIMIT above it. See docs/THREAT_MODEL.md.
ENGINES_IGNORING_LIMIT = frozenset({"duckdb"})
REGRESSIONS = Path(__file__).resolve().parents[1] / "corpus" / "fuzz_regressions.yaml"


@dataclass
class Failure:
    invariant: str
    engine: str
    dialect: str
    role: str
    sql: str
    rewritten: str | None
    detail: str

    def __str__(self) -> str:
        return (
            f"[{self.invariant}] on {self.engine} ({self.dialect}, role {self.role}): "
            f"{self.detail}\n  sql: {self.sql}\n  rewritten: {self.rewritten}"
        )


@dataclass
class EngineStats:
    checked: int = 0
    allowed: int = 0
    rejected: int = 0
    rejected_by_code: Counter[str] = field(default_factory=Counter)
    executed: int = 0
    engine_errors: int = 0
    returned_rows: int = 0
    hit_max_rows: int = 0
    nondeterministic: int = 0
    engine_ignored_limit: int = 0
    unrenderable: int = 0
    invariant_failures: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rejected_by_code"] = dict(sorted(self.rejected_by_code.items()))
        data["invariant_failures"] = {name: self.invariant_failures[name] for name in INVARIANTS}
        return data


@functools.cache
def guard_for(dialect: str, policy_json: str) -> Guard:
    return Guard(standard_policy(dialect, **json.loads(policy_json)))


def fuzz_guard(dialect: str) -> Guard:
    return guard_for(dialect, json.dumps(FUZZ_POLICY, sort_keys=True))


def _plain_limit(sql: str, dialect: str) -> int | None:
    """The literal row limit of the outermost query, if it is a plain one (no PERCENT/TIES)."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return None
    node = tree.args.get("limit")
    if not isinstance(node, (exp.Limit, exp.Fetch)):
        return None
    options = node.args.get("limit_options")
    if isinstance(options, exp.Expression) and (
        options.args.get("percent") or options.args.get("with_ties")
    ):
        return None
    count = node.args.get("expression") if isinstance(node, exp.Limit) else node.args.get("count")
    return int(count.name) if isinstance(count, exp.Literal) and count.is_int else None


def _ignored_limit(backend: Backend, sql: str, dialect: str, rows: list[Row]) -> bool:
    """Whether a known engine bug returned more rows than the plain LIMIT n <= max_rows in sql."""
    limit = _plain_limit(sql, dialect)
    return (
        backend.name in ENGINES_IGNORING_LIMIT
        and limit is not None
        and limit <= MAX_ROWS
        and len(rows) > limit
    )


def _plan_dependent(sql: str, dialect: str, rows: list[Row]) -> bool:
    """Whether which rows come back may depend on the engine's plan, not only on the data.

    True for a result cut by a limit or an offset, and for ``ROW_NUMBER`` or ``DISTINCT ON``
    (ties are broken by the plan). Two different SQL texts are then compared by row count only.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return True
    if tree.find(exp.RowNumber, exp.Offset) is not None:
        return True
    if any(d.args.get("on") is not None for d in tree.find_all(exp.Distinct)):
        return True
    for node in tree.find_all(exp.Limit, exp.Fetch):
        if node.parent is not tree:
            return True  # a limit inside a subquery
        count = node.args.get("expression") or node.args.get("count")
        if not (isinstance(count, exp.Literal) and count.is_int) or len(rows) >= int(count.name):
            return True
    return False


def _without_comments(sql: str, dialect: str) -> str:
    """sqlglot moves comments a little on every round trip; compare the SQL without them."""
    try:
        return sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect, comments=False)
    except SqlglotError:
        return sql


def _same_rows(left: list[Row], right: list[Row]) -> bool:
    return sorted(map(repr, left)) == sorted(map(repr, right))


def _run(backend: Backend, world: str, sql: str) -> tuple[bool, list[Row] | str]:
    try:
        return True, backend.query(world, sql)
    except EngineError as exc:
        return False, str(exc)


def check_example(
    sql: str,
    dialect: str,
    context: Mapping[str, Any],
    backend: Backend | None,
    stats: EngineStats | None,
    *,
    guard: Guard | None = None,
    role: str = "",
) -> list[Failure]:
    """Check one query for one engine. Returns the broken invariants (empty when all hold)."""
    guard = guard or fuzz_guard(dialect)
    engine = backend.name if backend is not None else dialect
    allowed_branches = set(context["user"]["branches"])
    stats = stats or EngineStats()
    stats.checked += 1

    def fail(invariant: str, detail: str, rewritten: str | None = None) -> Failure:
        stats.invariant_failures[invariant] += 1
        return Failure(invariant, engine, dialect, role, sql, rewritten, detail)

    try:
        result = guard.check(sql, context)
    except Exception as exc:  # invariant 4: any exception is a failure
        return [fail("fail_closed", f"check() raised {type(exc).__name__}: {exc}")]
    if not result.ok:
        stats.rejected += 1
        stats.rejected_by_code.update(code.value for code in result.codes)
        if not result.violations:
            return [fail("fail_closed", "rejected without a violation")]
        return []
    stats.allowed += 1
    rewritten = result.sql
    if rewritten is None:
        return [fail("fail_closed", "allowed without SQL")]
    failures: list[Failure] = []

    # Invariant 5 at the guard level: its own output is allowed and, if nothing is re-applied,
    # rewrites to itself.
    try:
        again = guard.check(rewritten, context)
    except Exception as exc:
        return [fail("fail_closed", f"check() of its own output raised {exc!r}", rewritten)]
    reapplied = bool(result.applied_filters)
    if not again.ok:
        codes = sorted(c.value for c in again.codes)
        failures.append(fail("idempotence", f"own output rejected: {codes}", rewritten))
    elif (
        again.sql != rewritten
        and not reapplied
        and (_without_comments(again.sql or "", dialect) != _without_comments(rewritten, dialect))
    ):
        reapplied = bool(guard.explain(sql, context).masked_columns)
        if not reapplied:
            failures.append(
                fail("idempotence", f"rewrote its own output to {again.sql}", rewritten)
            )

    if backend is None:
        return failures

    ok_a, out_a = _run(backend, "A", rewritten)
    ok_b, out_b = _run(backend, "B", rewritten)
    if not ok_a and not ok_b:
        stats.engine_errors += 1
        return failures
    if ok_a != ok_b:
        ok_a, out_a = _run(backend, "A", rewritten)
        ok_b, out_b = _run(backend, "B", rewritten)
        if ok_a != ok_b:
            error = out_b if ok_a else out_a
            world = "B" if ok_a else "A"
            failures.append(
                fail("non_interference", f"fails only in world {world}: {error}", rewritten)
            )
            return failures
        if not ok_a:
            stats.engine_errors += 1
            return failures
    assert isinstance(out_a, list) and isinstance(out_b, list)
    stats.executed += 1
    stats.returned_rows += len(out_a)
    if len(out_a) == MAX_ROWS:
        stats.hit_max_rows += 1

    for world, rows in (("A", out_a), ("B", out_b)):
        if len(rows) > MAX_ROWS:
            if _ignored_limit(backend, rewritten, dialect, rows):
                # The guard wrote LIMIT n <= max_rows and the engine returned more: an engine bug,
                # counted and reported (docs/THREAT_MODEL.md), not a guard failure.
                stats.engine_ignored_limit += 1
                break
            failures.append(fail("max_rows", f"{len(rows)} rows in world {world}", rewritten))
            break
    found = leaks(out_a, allowed_branches) + leaks(out_b, allowed_branches)
    for invariant in dict.fromkeys(name for name, _ in found):
        cells = sorted({cell for name, cell in found if name == invariant})[:3]
        failures.append(fail(invariant, f"canary in the output: {cells}", rewritten))

    # Same SQL, same plan, same visible data: the same rows in the same order.
    if out_a != out_b:
        # Rule out a nondeterministic query before calling it a leak.
        _, again_a = _run(backend, "A", rewritten)
        _, again_b = _run(backend, "B", rewritten)
        if again_a != out_a or again_b != out_b:
            stats.nondeterministic += 1
        else:
            detail = f"worlds differ: A={out_a[:4]} B={out_b[:4]}"
            failures.append(fail("non_interference", detail, rewritten))

    if again.ok and again.sql is not None and again.sql != rewritten:
        ok_again, out_again = _run(backend, "A", again.sql)
        if not ok_again:
            detail = f"re-rewritten SQL fails on the engine: {out_again}"
            failures.append(fail("idempotence", detail, rewritten))
        elif isinstance(out_again, list) and (
            _ignored_limit(backend, again.sql, dialect, out_again)
            or _ignored_limit(backend, rewritten, dialect, out_a)
        ):
            stats.engine_ignored_limit += 1
        elif isinstance(out_again, list) and (
            len(out_again) != len(out_a)
            if _plan_dependent(rewritten, dialect, out_a)
            else not _same_rows(out_again, out_a)
        ):
            detail = f"re-rewritten SQL returns other rows: {out_again[:4]} vs {out_a[:4]}"
            failures.append(fail("idempotence", detail, rewritten))
    return failures


# -- regressions --------------------------------------------------------------------------------


def load_regressions() -> list[dict[str, Any]]:
    if not REGRESSIONS.exists():
        return []
    return list(yaml.safe_load(REGRESSIONS.read_text(encoding="utf-8")) or [])


def role_of(context: Mapping[str, Any]) -> str:
    return next((name for name, value in ROLES.items() if value == context), "custom")


def record(failures: list[Failure], seed: int, profile: str) -> list[str]:
    """Append minimised failures to tests/corpus/fuzz_regressions.yaml; return their ids.

    New entries are ``pending`` until triaged: the corpus test skips them, but the fuzz
    regression test replays every entry through the oracle and keeps failing until the guard is
    fixed. Triage sets ``expect`` (and ``codes``/``contains``) and removes ``pending``.
    """
    existing = load_regressions()
    known = {(case["dialect"], case["sql"]) for case in existing}
    text = REGRESSIONS.read_text(encoding="utf-8") if REGRESSIONS.exists() else ""
    if text.strip() in ("", "[]"):
        text = HEADER
    ids: list[str] = []
    for failure in failures:
        if (failure.dialect, failure.sql) in known:
            continue
        known.add((failure.dialect, failure.sql))
        digest = hashlib.sha256(f"{failure.dialect}\n{failure.sql}".encode()).hexdigest()[:8]
        case_id = f"fuzz-{failure.dialect}-{digest}"
        entry = {
            "id": case_id,
            "dialect": failure.dialect,
            "sql": failure.sql,
            "expect": "reject",
            "context": ROLES.get(failure.role) or ROLES["north"],
            "pending": (
                f"found by fuzzing ({failure.invariant} on {failure.engine}, {profile} profile, "
                f"seed {seed}); triage: set expect, then remove pending"
            ),
            "note": f"{failure.detail[:300]} | rewritten: {failure.rewritten}",
        }
        block = yaml.safe_dump(
            [entry], sort_keys=False, allow_unicode=True, width=100, default_flow_style=None
        )
        anchor = "&fuzz_policy" not in text
        policy = (
            "  policy: &fuzz_policy\n"
            + _indent(yaml.safe_dump(FUZZ_POLICY, sort_keys=False, default_flow_style=None), 4)
            if anchor
            else "  policy: *fuzz_policy\n"
        )
        text = text.rstrip("\n") + "\n" + block + policy
        ids.append(case_id)
    if ids:
        REGRESSIONS.write_text(text, encoding="utf-8")
    return ids


def _indent(text: str, spaces: int) -> str:
    return "".join(" " * spaces + line + "\n" for line in text.splitlines())


HEADER = """\
# Minimised failures found by the fuzzer (tests/fuzz), kept as regression cases for ever.
# tests/test_corpus.py and the conformance export run them like any corpus case;
# tests/fuzz/test_fuzz.py also replays each one through the canary oracle on its engine.
# The policy is tests/conftest.py's standard_policy plus tests/fuzz/fuzz_schema.py FUZZ_POLICY.
"""
