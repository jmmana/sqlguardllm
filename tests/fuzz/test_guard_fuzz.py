"""Execute generated rewritten queries and check their output against canary data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st

from fuzz.strategies import TABLES, query_asts
from fuzz.support import CONTEXT, EXAMPLES, SEED, record_failure, record_result
from sqlguardllm import Guard, Policy

MAX_ROWS = 4
CANARIES = {
    *(
        f"CANARY_TENANT3_{table}_{row_index}_{field}"
        for table in TABLES
        for row_index in (1, 2)
        for field in ("ID", "RELATED", "LABEL", "PAYLOAD")
    ),
    "CANARY_TENANT3_BRANCH",
    *(
        f"CANARY_{kind}_{table}_{branch}_{row_index}"
        for kind in ("HIDDEN", "MASKED")
        for table in TABLES
        for branch in (1, 2, 3)
        for row_index in (1, 2)
    ),
}
REGRESSIONS = Path(__file__).resolve().parents[1] / "corpus" / "fuzz_regressions.yaml"


def _assert_output_invariants(
    engine_name: str,
    dialect: str,
    sql: str,
    rows: list[tuple[Any, ...]],
    max_rows: int,
) -> None:
    rendered = [str(value).upper() for row in rows for value in row if value is not None]
    leaks = {canary for canary in CANARIES if any(canary in value for value in rendered)}
    checks = {
        "other-tenant-canary": not any("CANARY_TENANT3_" in item for item in leaks),
        "hidden-or-masked-canary": not any(
            "CANARY_HIDDEN_" in item or "CANARY_MASKED_" in item for item in leaks
        ),
        "max-rows": len(rows) <= max_rows,
    }
    for invariant, passed in checks.items():
        if not passed:
            record_failure(engine_name, dialect, sql, invariant)
    assert all(checks.values()), f"canary/row-limit invariant failed: {checks}; leaks={leaks}"


@settings(
    max_examples=EXAMPLES,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@seed(SEED)
@given(data=st.data())
def test_generated_queries_do_not_leak(fuzz_engine: Any, fuzz_guard: Any, data: Any) -> None:
    engine_name, engine = fuzz_engine
    dialect = engine_name
    ast = data.draw(query_asts(dialect), label="query AST")
    sql = ast.sql(dialect=dialect)

    try:
        result = fuzz_guard.check(sql, CONTEXT)
    except Exception as exc:
        record_result(engine_name, False, {"unexpected_exception"})
        record_failure(engine_name, dialect, sql, "unexpected-exception")
        pytest.fail(f"guard raised {type(exc).__name__} for generated SQL: {sql}", pytrace=True)

    record_result(engine_name, result.ok, {code.value for code in result.codes})
    if not result.ok:
        return

    if result.sql is None:
        record_failure(engine_name, dialect, sql, "unexpected-exception")
        pytest.fail(f"guard allowed SQL without returning a rewrite: {sql}")
    try:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(result.sql).fetchall()
    except Exception as exc:
        record_failure(engine_name, dialect, sql, "engine-execution")
        pytest.fail(f"allowed SQL failed on {engine_name}: {exc}: {result.sql}", pytrace=True)

    _assert_output_invariants(engine_name, dialect, sql, rows, MAX_ROWS)

    try:
        rewritten = fuzz_guard.check(result.sql, CONTEXT)
    except Exception as exc:
        record_failure(engine_name, dialect, sql, "unexpected-exception")
        pytest.fail(
            f"guard raised {type(exc).__name__} on rewritten SQL: {result.sql}", pytrace=True
        )
    idempotent = rewritten.ok and rewritten.sql == result.sql
    if not idempotent:
        record_failure(engine_name, dialect, sql, "idempotence")
    assert idempotent, f"rewritten SQL is not idempotent: {result.sql}"


def test_canary_fixture_has_filtered_join_rows(fuzz_engine: Any, fuzz_guard: Any) -> None:
    engine_name, engine = fuzz_engine
    sql = (
        "SELECT i.label, c.label FROM invoices AS i "
        "JOIN customers AS c ON i.branch_id = c.branch_id"
    )
    result = fuzz_guard.check(sql, CONTEXT)
    record_result(engine_name, result.ok, {code.value for code in result.codes})
    assert result.ok and result.sql is not None, result.messages
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(result.sql).fetchall()
    assert rows
    _assert_output_invariants(engine_name, engine_name, sql, rows, MAX_ROWS)


def test_saved_fuzz_regressions_replay(fuzz_engine: Any) -> None:
    engine_name, engine = fuzz_engine
    cases = yaml.safe_load(REGRESSIONS.read_text(encoding="utf-8")) or []
    for case in cases:
        if case["dialect"] != engine_name:
            continue
        guard = Guard(Policy(dialect=engine_name, **case.get("policy", {})))
        context = case.get("context", CONTEXT)
        try:
            result = guard.check(case["sql"], context)
        except Exception as exc:
            record_result(engine_name, False, {"unexpected_exception"})
            record_failure(engine_name, engine_name, case["sql"], "unexpected-exception")
            pytest.fail(f"guard raised {type(exc).__name__} for saved case {case['id']}")

        record_result(engine_name, result.ok, {code.value for code in result.codes})
        if case["expect"] == "reject":
            if result.ok:
                pytest.fail(f"saved case should be rejected: {case['id']}")
            continue

        if not result.ok or result.sql is None:
            pytest.fail(f"saved case should be allowed: {case['id']}")
        try:
            with engine.connect() as connection:
                rows = connection.exec_driver_sql(result.sql).fetchall()
        except Exception as exc:
            record_failure(engine_name, engine_name, case["sql"], "engine-execution")
            pytest.fail(f"saved SQL failed on {engine_name}: {exc}")
        max_rows = guard.policy.max_rows or len(rows)
        _assert_output_invariants(engine_name, engine_name, case["sql"], rows, max_rows)
        rewritten = guard.check(result.sql, context)
        assert rewritten.ok and rewritten.sql == result.sql, f"not idempotent: {case['id']}"
