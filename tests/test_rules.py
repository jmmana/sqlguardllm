"""Unit tests for sources, functions and limits that the corpus doesn't cover."""

from __future__ import annotations

import pytest

from conftest import CONTEXT, standard_policy
from sqlguard import Guard, Policy, ViolationCode


def test_result_lists_physical_tables(guard: Guard) -> None:
    result = guard.check(
        "SELECT b.name FROM branches b JOIN invoices i ON i.branch_id = b.id", CONTEXT
    )
    assert result.ok
    assert result.tables == frozenset({"branches", "invoices"})


def test_default_schema_matches_both_forms() -> None:
    guard = Guard(standard_policy("tsql"))
    for sql in ("SELECT name FROM branches", "SELECT name FROM dbo.branches"):
        assert guard.check(sql).ok, sql
    assert not guard.check("SELECT name FROM sales.branches").ok


def test_snowflake_identifier_case() -> None:
    guard = Guard(Policy(dialect="snowflake", tables={"invoices"}, max_rows=None))
    assert guard.check("SELECT id FROM INVOICES").ok
    assert guard.check("SELECT id FROM invoices").ok
    # Quoted lowercase is a different table in Snowflake.
    assert ViolationCode.TABLE_NOT_ALLOWED in guard.check('SELECT id FROM "invoices"').codes


def test_tsql_is_case_insensitive() -> None:
    guard = Guard(Policy(dialect="tsql", tables={"Invoices"}, max_rows=None))
    assert guard.check("SELECT id FROM INVOICES").ok
    assert guard.check("SELECT id FROM [invoices]").ok


def test_no_table_allowlist() -> None:
    guard = Guard(Policy(dialect="postgres", max_rows=None))
    assert guard.check("SELECT * FROM anything").ok
    assert not guard.check("DELETE FROM anything").ok


def test_extra_denied_function_qualified() -> None:
    guard = Guard(standard_policy(extra_denied_functions={"refresh_sales_cache"}))
    for sql in ("SELECT refresh_sales_cache()", "SELECT public.refresh_sales_cache()"):
        assert ViolationCode.DENIED_FUNCTION in guard.check(sql, CONTEXT).codes, sql


def test_replaced_denylist() -> None:
    guard = Guard(standard_policy(denied_functions={"upper"}))
    assert not guard.check("SELECT UPPER(name) FROM branches").ok
    assert guard.check("SELECT pg_sleep(1)").ok  # the built-in list was replaced


def test_allowlist_mode() -> None:
    guard = Guard(standard_policy(function_policy="allowlist", allowed_functions={"count", "sum"}))
    assert guard.check("SELECT COUNT(*), SUM(total) FROM invoices", CONTEXT).ok
    result = guard.check("SELECT AVG(total) FROM invoices", CONTEXT)
    assert result.codes == {ViolationCode.DENIED_FUNCTION}


def test_all_violations_are_reported(guard: Guard) -> None:
    result = guard.check("SELECT email, pg_sleep(1) FROM customers JOIN secrets ON true", CONTEXT)
    assert result.codes >= {
        ViolationCode.HIDDEN_COLUMN,
        ViolationCode.DENIED_FUNCTION,
        ViolationCode.TABLE_NOT_ALLOWED,
    }


@pytest.mark.parametrize(
    ("dialect", "sql", "expected"),
    [
        ("postgres", "SELECT id FROM t", "SELECT id FROM t LIMIT 100"),
        ("mysql", "SELECT id FROM t", "SELECT id FROM t LIMIT 100"),
        ("sqlite", "SELECT id FROM t", "SELECT id FROM t LIMIT 100"),
        ("tsql", "SELECT id FROM t", "SELECT TOP 100 id FROM t"),
        ("oracle", "SELECT id FROM t", "SELECT id FROM t FETCH FIRST 100 ROWS ONLY"),
        ("snowflake", "SELECT id FROM t", "SELECT id FROM t LIMIT 100"),
        ("bigquery", "SELECT id FROM t", "SELECT id FROM t LIMIT 100"),
        ("tsql", "SELECT TOP 5000 id FROM t", "SELECT TOP 100 id FROM t"),
        (
            "oracle",
            "SELECT id FROM t FETCH FIRST 5000 ROWS ONLY",
            "SELECT id FROM t FETCH FIRST 100 ROWS ONLY",
        ),
        ("postgres", "SELECT id FROM t LIMIT ALL", "SELECT id FROM t LIMIT 100"),
    ],
)
def test_limit_per_dialect(dialect: str, sql: str, expected: str) -> None:
    result = Guard(Policy(dialect=dialect, tables={"t"}, max_rows=100)).check(sql)
    assert result.ok, result.messages
    assert result.sql == expected


def test_no_limit_when_disabled() -> None:
    result = Guard(Policy(dialect="postgres", tables={"t"}, max_rows=None)).check(
        "SELECT id FROM t"
    )
    assert result.sql == "SELECT id FROM t"


def test_pretty_output() -> None:
    result = Guard(Policy(dialect="postgres", tables={"t"}), pretty=True).check("SELECT id FROM t")
    assert result.sql is not None and "\n" in result.sql
