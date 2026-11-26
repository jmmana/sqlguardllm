"""The same baseline attacks and the same allowed queries, parsed in every supported dialect."""

from __future__ import annotations

import pytest

from conftest import CONTEXT, standard_policy
from sqlguardllm import Guard, ViolationCode

DIALECTS = [
    "postgres", "mysql", "tsql", "oracle", "sqlite",
    "duckdb", "snowflake", "bigquery", "trino", "clickhouse",
]  # fmt: skip

REJECTED = [
    ("DELETE FROM invoices", ViolationCode.NOT_A_QUERY),
    ("UPDATE invoices SET total = 0", ViolationCode.NOT_A_QUERY),
    ("DROP TABLE invoices", ViolationCode.NOT_A_QUERY),
    ("SELECT 1; SELECT 2", ViolationCode.MULTIPLE_STATEMENTS),
    ("SELECT email FROM customers", ViolationCode.HIDDEN_COLUMN),
    ("SELECT * FROM customers", ViolationCode.HIDDEN_COLUMN),
    ("SELECT id FROM secrets", ViolationCode.TABLE_NOT_ALLOWED),
    ("SELECT name FROM customers WHERE email = 'a'", ViolationCode.HIDDEN_COLUMN),
    ("SELECT COUNT(*) FROM customers HAVING MAX(tax_id) > 'a'", ViolationCode.HIDDEN_COLUMN),
]
ALLOWED = [
    "SELECT id FROM invoices",
    "SELECT b.name, SUM(i.total) FROM invoices i JOIN branches b ON b.id = i.branch_id "
    "GROUP BY b.name",
    "WITH t AS (SELECT id FROM invoices) SELECT COUNT(*) FROM t",
    "SELECT id FROM invoices WHERE total > (SELECT AVG(total) FROM invoices)",
]  # fmt: skip


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(("sql", "code"), REJECTED)
def test_rejected_in_every_dialect(dialect: str, sql: str, code: ViolationCode) -> None:
    result = Guard(standard_policy(dialect)).check(sql, CONTEXT)
    assert not result.ok, f"{dialect}: accepted {result.sql}"
    assert code in result.codes, f"{dialect}: {sorted(c.value for c in result.codes)}"


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_in_every_dialect_with_filter_and_limit(dialect: str, sql: str) -> None:
    result = Guard(standard_policy(dialect)).check(sql, CONTEXT)
    assert result.ok, f"{dialect}: {result.messages}"
    assert result.sql is not None
    assert "branch_id IN (1, 2)" in result.sql
    assert "1000" in result.sql  # the row limit, in the dialect's own syntax
