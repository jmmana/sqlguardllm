"""Unit tests for sources, functions and limits that the corpus doesn't cover."""

from __future__ import annotations

import pytest

from conftest import CONTEXT, standard_policy
from sqlguardllm import Guard, Policy, ViolationCode


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


@pytest.mark.parametrize(
    ("suffix", "expected", "limit"),
    [
        ("", "OFFSET 0 ROWS FETCH NEXT 2 ROWS ONLY", 2),
        (" OFFSET 4 ROWS", "OFFSET 4 ROWS FETCH NEXT 2 ROWS ONLY", 2),
        (" OFFSET 4 ROWS FETCH FIRST (1) ROW ONLY", "OFFSET 4 ROWS FETCH NEXT 1 ROWS ONLY", 1),
        (" OFFSET 4 ROWS FETCH NEXT 5 ROWS ONLY", "OFFSET 4 ROWS FETCH NEXT 2 ROWS ONLY", 2),
    ],
)
def test_tsql_ordered_set_limit(suffix: str, expected: str, limit: int) -> None:
    base = "SELECT id FROM t UNION SELECT id FROM u ORDER BY 1 DESC"
    guard = Guard(Policy(dialect="tsql", tables={"t", "u"}, max_rows=2))
    result = guard.explain(base + suffix)
    assert result.ok, result.violations
    assert result.sql == f"{base} {expected}"
    assert result.limit == limit


def test_tsql_ordered_set_rejects_non_allowlisted_branch() -> None:
    guard = Guard(Policy(dialect="tsql", tables={"invoices"}, max_rows=2))
    result = guard.check("SELECT id FROM invoices UNION SELECT id FROM other_invoices ORDER BY 1")
    assert result.sql is None
    assert ViolationCode.TABLE_NOT_ALLOWED in result.codes


def test_ordered_set_limit_follows_output_dialect() -> None:
    guard = Guard(Policy(dialect="mysql", output_dialect="tsql", tables={"t", "u"}, max_rows=2))
    base = "SELECT id FROM t UNION SELECT id FROM u ORDER BY id DESC"
    result = guard.check(f"{base} LIMIT 1 OFFSET 4")
    assert result.ok, result.messages
    assert result.sql == f"{base} OFFSET 4 ROWS FETCH NEXT 1 ROWS ONLY"


def test_ordered_set_zero_limit_to_tsql() -> None:
    guard = Guard(Policy(dialect="mysql", output_dialect="tsql", tables={"t", "u"}, max_rows=2))
    result = guard.explain("SELECT id FROM t UNION SELECT id FROM u ORDER BY id LIMIT 0 OFFSET 4")
    assert result.ok, result.violations
    assert result.sql is not None and result.sql.startswith("SELECT TOP 0 ")
    assert "FETCH" not in result.sql
    assert result.limit == 0


@pytest.mark.parametrize(
    ("dialect", "max_rows", "suffix", "expected"),
    [
        ("tsql", None, " ORDER BY 1", "SELECT id FROM t UNION SELECT id FROM u ORDER BY 1"),
        (
            "tsql",
            2,
            "",
            "SELECT TOP 2 * FROM (SELECT id FROM t UNION SELECT id FROM u) AS sqlguard_rows",
        ),
        (
            "postgres",
            2,
            " ORDER BY 1",
            "SELECT * FROM (SELECT id FROM t UNION SELECT id FROM u ORDER BY 1)"
            " AS sqlguard_rows LIMIT 2",
        ),
    ],
)
def test_set_limit_other_paths(
    dialect: str, max_rows: int | None, suffix: str, expected: str
) -> None:
    guard = Guard(Policy(dialect=dialect, tables={"t", "u"}, max_rows=max_rows))
    result = guard.check(f"SELECT id FROM t UNION SELECT id FROM u{suffix}")
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


@pytest.mark.parametrize(
    ("dialect", "output", "sql", "expected"),
    [
        (
            "postgres",
            "tsql",
            "SELECT id, total FROM invoices ORDER BY total DESC",
            # sqlglot keeps PostgreSQL's NULLS FIRST for DESC with a CASE in SQL Server.
            "SELECT TOP 1000 id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1, 2))"
            " AS invoices ORDER BY CASE WHEN total IS NULL THEN 1 ELSE 0 END DESC, total DESC",
        ),
        (
            "postgres",
            "oracle",
            "SELECT id FROM invoices WHERE issued_at >= CURRENT_DATE - INTERVAL '7 days'",
            "FETCH FIRST 1000 ROWS ONLY",
        ),
        (
            "mysql",
            "postgres",
            "SELECT `id`, IFNULL(total, 0) AS total FROM invoices",
            'SELECT "id", COALESCE(total, 0) AS total FROM (SELECT * FROM invoices WHERE branch_id'
            " IN (1, 2)) AS invoices LIMIT 1000",
        ),
    ],
)
def test_output_dialect(dialect: str, output: str, sql: str, expected: str) -> None:
    result = Guard(standard_policy(dialect, output_dialect=output)).check(sql, CONTEXT)
    assert result.ok, result.messages
    assert result.sql is not None
    assert expected in result.sql


def test_output_dialect_keeps_parsing_in_the_input_dialect() -> None:
    # T-SQL syntax is not valid input when the policy reads PostgreSQL.
    guard = Guard(standard_policy("postgres", output_dialect="tsql"))
    result = guard.check("SELECT TOP 5 id FROM invoices", CONTEXT)
    assert result.codes == {ViolationCode.PARSE_ERROR}


def test_new_codes_have_spanish_messages() -> None:
    guard = Guard(
        standard_policy(max_joins=0, required_filters={"invoices": ["issued_at"]}), language="es"
    )
    result = guard.check(
        "SELECT b.name FROM invoices i JOIN branches b ON b.id = i.branch_id", CONTEXT
    )
    assert result.codes == {ViolationCode.TOO_COMPLEX, ViolationCode.MISSING_REQUIRED_FILTER}
    assert all("consulta" in m or "Leer" in m for m in result.messages)
