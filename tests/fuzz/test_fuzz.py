"""Property-based fuzzing of the guard, judged by executing the output on canary data.

Profiles (``SQLGUARD_FUZZ_PROFILE``):

- ``pr`` (default, every pull request): 300 examples on SQLite and DuckDB, fixed seed.
- ``deep`` (nightly and manual, .github/workflows/engines.yml): 20,000 examples, a new seed each
  run, on SQLite, DuckDB and every engine whose URL is set (PostgreSQL 16, MySQL 8, SQL Server
  2022); with ``SQLGUARD_REQUIRE_ENGINES=1`` a missing engine fails.

``SQLGUARD_FUZZ_SEED``, ``SQLGUARD_FUZZ_EXAMPLES`` and ``SQLGUARD_FUZZ_ENGINES`` (comma-separated)
override the profile; the seed and count are printed so any run replays exactly.
``SQLGUARD_FUZZ_STATS=path`` writes the run statistics as JSON (research/results/fuzz.json).
``SQLGUARD_FUZZ_TRACE=path`` keeps the statement each engine is running, for a crashing engine.

A failure is shrunk by hypothesis and appended to tests/corpus/fuzz_regressions.yaml.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any

import pytest
from fuzz_engines import ALL_ENGINES, Backend, open_backend
from fuzz_oracle import (
    INVARIANTS,
    EngineStats,
    Failure,
    check_example,
    guard_for,
    load_regressions,
    record,
    role_of,
)
from fuzz_schema import ROLES
from fuzz_sql import FuzzCase, fuzz_cases, render
from hypothesis import HealthCheck, given, seed, settings

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # "X is not supported by Y" on rendering

PROFILES = {"pr": (300, ("sqlite", "duckdb")), "deep": (20_000, ALL_ENGINES)}
PROFILE = os.environ.get("SQLGUARD_FUZZ_PROFILE") or "pr"
if PROFILE not in PROFILES:
    raise ValueError(f"SQLGUARD_FUZZ_PROFILE must be one of {', '.join(PROFILES)}")
EXAMPLES = int(os.environ.get("SQLGUARD_FUZZ_EXAMPLES") or PROFILES[PROFILE][0])
_seed = os.environ.get("SQLGUARD_FUZZ_SEED")
SEED = int(_seed) if _seed else (62 if PROFILE == "pr" else int.from_bytes(os.urandom(4), "big"))
ENGINES = tuple(
    name.strip()
    for name in (os.environ.get("SQLGUARD_FUZZ_ENGINES") or ",".join(PROFILES[PROFILE][1])).split(
        ","
    )
    if name.strip()
)
REPLAY = (
    f"SQLGUARD_FUZZ_PROFILE={PROFILE} SQLGUARD_FUZZ_SEED={SEED} SQLGUARD_FUZZ_EXAMPLES={EXAMPLES} "
    f"SQLGUARD_FUZZ_ENGINES={','.join(ENGINES)} uv run pytest tests/fuzz -m fuzz"
)

pytestmark = pytest.mark.fuzz


def _say(request: pytest.FixtureRequest, line: str) -> None:
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)
    else:  # pragma: no cover - pytest without its terminal plugin
        print(line)


@pytest.fixture(scope="module")
def backends() -> Iterator[list[Backend]]:
    opened: list[Backend] = []
    for name in ENGINES:
        if name not in ALL_ENGINES:
            raise ValueError(f"unknown engine {name!r}; use {', '.join(ALL_ENGINES)}")
        if name == "duckdb":
            pytest.importorskip("duckdb")
        backend = open_backend(name)
        if backend is None:
            if os.environ.get("SQLGUARD_REQUIRE_ENGINES") == "1":
                pytest.fail(f"{name}: URL not set")
            continue
        opened.append(backend)
    yield opened
    for backend in opened:
        backend.close()


def test_fuzz(backends: list[Backend], request: pytest.FixtureRequest) -> None:
    engines = [b.name for b in backends]
    _say(
        request,
        f"\nfuzz: profile={PROFILE} seed={SEED} examples={EXAMPLES} engines={','.join(engines)}"
        f"\nfuzz: replay with {REPLAY}",
    )
    stats = {name: EngineStats() for name in engines}
    shortest: dict[tuple[str, str], Failure] = {}
    state = {"examples": 0, "failed": False}
    started = time.monotonic()

    @seed(SEED)
    @settings(
        max_examples=EXAMPLES,
        deadline=None,
        database=None,
        print_blob=True,
        suppress_health_check=list(HealthCheck),
    )
    @given(case=fuzz_cases())
    def run(case: FuzzCase) -> None:
        counting = not state["failed"]  # stop counting once shrinking starts
        state["examples"] += counting
        failures: list[Failure] = []
        for backend in backends:
            engine_stats = stats[backend.name] if counting else EngineStats()
            sql = render(case, backend.dialect)
            if sql is None:
                engine_stats.unrenderable += 1
                continue
            failures += check_example(
                sql, backend.dialect, ROLES[case.role], backend, engine_stats, role=case.role
            )
        if failures:
            state["failed"] = True
            for failure in failures:
                key = (failure.invariant, failure.engine)
                if key not in shortest or len(failure.sql) < len(shortest[key].sql):
                    shortest[key] = failure
            raise AssertionError("\n".join(str(f) for f in failures) + f"\nreplay: {REPLAY}")

    try:
        run()
    finally:
        elapsed = time.monotonic() - started
        summary = _summary(stats, state["examples"], elapsed, not state["failed"])
        for name, data in summary["engines"].items():
            _say(
                request,
                f"fuzz: {name}: {data['allowed']} allowed, {data['rejected']} rejected, "
                f"{data['executed']} executed, {data['engine_errors']} engine errors, "
                f"failures {sum(data['invariant_failures'].values())}"
                + (
                    f", {data['engine_ignored_limit']} results where the ENGINE ignored the LIMIT"
                    if data["engine_ignored_limit"]
                    else ""
                ),
            )
        _say(request, f"fuzz: {state['examples']} examples in {elapsed:.1f} s")
        path = os.environ.get("SQLGUARD_FUZZ_STATS")
        if path:
            with open(path, "w", encoding="utf-8") as out:
                json.dump(summary, out, indent=2, sort_keys=True)
                out.write("\n")
        if shortest:
            ids = record(list(shortest.values()), SEED, PROFILE)
            _say(request, f"fuzz: recorded {ids} in tests/corpus/fuzz_regressions.yaml")


def _summary(
    stats: dict[str, EngineStats], examples: int, elapsed: float, complete: bool
) -> dict[str, Any]:
    engines = {name: s.as_dict() for name, s in stats.items()}
    totals = {
        name: sum(e["invariant_failures"][name] for e in engines.values()) for name in INVARIANTS
    }
    packages = {}
    for package in ("sqlguardllm", "sqlglot", "hypothesis", "duckdb"):
        try:
            packages[package] = version(package)
        except Exception:  # pragma: no cover - optional package missing
            packages[package] = None
    return {
        "profile": PROFILE,
        "seed": SEED,
        "examples_requested": EXAMPLES,
        "examples": examples,
        "complete": complete,
        "engines": engines,
        "invariant_failures": totals,
        "duration_s": round(elapsed, 1),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "packages": packages,
        "roles": ROLES,
        "replay": REPLAY,
    }


# -- every recorded failure stays a regression case --------------------------------------------

REGRESSIONS = load_regressions()


@pytest.fixture(scope="module")
def engines_by_name() -> Iterator[dict[str, Backend]]:
    """SQLite, DuckDB and the engine of every recorded regression whose URL is set."""
    wanted = {"sqlite", "duckdb"} | {case["dialect"] for case in REGRESSIONS}
    opened: dict[str, Backend] = {}
    for name in ALL_ENGINES:
        if name not in wanted:
            continue
        if name == "duckdb":
            pytest.importorskip("duckdb")
        backend = open_backend(name)
        if backend is not None:
            opened[name] = backend
    yield opened
    for backend in opened.values():
        backend.close()


@pytest.mark.parametrize("case", REGRESSIONS, ids=[case["id"] for case in REGRESSIONS])
def test_fuzz_regression(case: dict[str, Any], engines_by_name: dict[str, Backend]) -> None:
    """Every invariant holds for every recorded failure, on its engine when it is available."""
    context = case.get("context") or ROLES["north"]
    guard = guard_for(case["dialect"], json.dumps(case.get("policy") or {}, sort_keys=True))
    failures = check_example(
        case["sql"],
        case["dialect"],
        context,
        engines_by_name.get(case["dialect"]),
        None,
        guard=guard,
        role=role_of(context),
    )
    assert not failures, "\n".join(str(f) for f in failures)


# -- the oracle catches the bypasses found by hand before the fuzzer existed -------------------

PAST_BYPASSES = [
    # 0.2.0a2: unqualified hidden columns in HAVING were not checked; the output never shows the
    # value, but it depends on it (non-interference).
    (
        "scope_columns",
        "sqlite",
        "SELECT COUNT(*) FROM customers HAVING MAX(email) > 'm'",
        "non_interference",
    ),
    # 0.2.0a2: an expression over a name that is both an alias and a hidden column in ORDER BY;
    # the row limit cuts the result in an order that depends on the hidden values.
    (
        "scope_columns",
        "sqlite",
        "SELECT name AS email FROM customers ORDER BY LOWER(email)",
        "non_interference",
    ),
    # 0.3.0a1: LIMIT … PERCENT kept its flag, so max_rows was a percentage.
    (
        "inexact_limit",
        "duckdb",
        "SELECT c.id FROM customers AS c CROSS JOIN customers AS d LIMIT 50 PERCENT",
        "max_rows",
    ),
]


@pytest.mark.parametrize("bypass", PAST_BYPASSES, ids=[b[2] for b in PAST_BYPASSES])
def test_oracle_catches_past_bypasses(
    bypass: tuple[str, str, str, str],
    engines_by_name: dict[str, Backend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a past fix reverted, the oracle reports the bypass; with the fix, nothing."""
    revert, engine, sql, invariant = bypass
    backend = engines_by_name.get(engine)
    if backend is None:
        pytest.skip(f"{engine} is not available")
    context = ROLES["north"]
    assert check_example(sql, engine, context, backend, None) == []

    if revert == "scope_columns":
        for module in ("sqlguardllm.rules.columns", "sqlguardllm.rules.masking"):
            monkeypatch.setattr(f"{module}.scope_columns", lambda scope: list(scope.columns))
    else:
        monkeypatch.setattr("sqlguardllm.rules.limit._inexact", lambda limit: False)
    failures = check_example(sql, engine, context, backend, None)
    assert invariant in {f.invariant for f in failures}, failures
