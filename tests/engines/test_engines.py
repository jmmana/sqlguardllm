"""The rewritten SQL really runs, and really filters, on real engines.

DuckDB and SQLite run everywhere. PostgreSQL, MySQL and SQL Server run when their URL is set
(``SQLGUARD_PG_URL``, ``SQLGUARD_MYSQL_URL``, ``SQLGUARD_MSSQL_URL``); CI sets them with service
containers (.github/workflows/engines.yml). With SQLGUARD_REQUIRE_ENGINES=1 a missing engine fails.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

sa = pytest.importorskip("sqlalchemy")

from sqlguardllm import Guard, Policy  # noqa: E402

ENGINES = {
    "sqlite": ("sqlite", "sqlite://"),
    "duckdb": ("duckdb", "duckdb:///:memory:"),
    "postgres": ("postgres", os.environ.get("SQLGUARD_PG_URL")),
    "mysql": ("mysql", os.environ.get("SQLGUARD_MYSQL_URL")),
    "tsql": ("tsql", os.environ.get("SQLGUARD_MSSQL_URL")),
}
ROWS = [(1, 1, 100), (2, 1, 50), (3, 2, 70), (4, 3, 900)]  # id, branch_id, total
CONTEXT = {"user": {"branches": [1, 2]}}


@pytest.fixture(params=list(ENGINES))
def engine(request: pytest.FixtureRequest) -> Iterator[tuple[str, Any]]:
    dialect, url = ENGINES[request.param]
    if not url:
        if os.environ.get("SQLGUARD_REQUIRE_ENGINES") == "1":
            pytest.fail(f"{request.param}: URL not set")
        pytest.skip(f"{request.param}: URL not set")
    if request.param == "duckdb":
        pytest.importorskip("duckdb_engine")
    eng = sa.create_engine(url)
    with eng.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS sg_invoices"))
        conn.execute(sa.text("CREATE TABLE sg_invoices (id INT, branch_id INT, total INT)"))
        for row in ROWS:
            conn.execute(
                sa.text("INSERT INTO sg_invoices VALUES (:i, :b, :t)"),
                {"i": row[0], "b": row[1], "t": row[2]},
            )
    yield dialect, eng
    with eng.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS sg_invoices"))
    eng.dispose()


def _run(dialect: str, eng: Any, sql: str, **policy: Any) -> list[tuple[Any, ...]]:
    guard = Guard(
        Policy(
            dialect=dialect,
            tables={"sg_invoices"},
            row_filters={"sg_invoices": "branch_id IN ({user.branches})"},
            default_schema="dbo" if dialect == "tsql" else None,
            **policy,
        )
    )
    result = guard.check(sql, CONTEXT)
    assert result.ok, result.messages
    assert result.sql is not None
    with eng.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.text(result.sql.replace("%", "%%"))).fetchall()]


def test_row_filter_returns_only_the_users_rows(engine: tuple[str, Any]) -> None:
    dialect, eng = engine
    rows = _run(dialect, eng, "SELECT id FROM sg_invoices ORDER BY id")
    assert [r[0] for r in rows] == [1, 2, 3]  # branch 3 never comes back


def test_aggregate_over_filtered_rows(engine: tuple[str, Any]) -> None:
    dialect, eng = engine
    rows = _run(dialect, eng, "SELECT SUM(total) FROM sg_invoices")
    assert int(rows[0][0]) == 220  # 100 + 50 + 70, not 1120


def test_limit_in_the_engines_own_syntax(engine: tuple[str, Any]) -> None:
    dialect, eng = engine
    rows = _run(dialect, eng, "SELECT id FROM sg_invoices ORDER BY id", max_rows=2)
    assert [r[0] for r in rows] == [1, 2]


def test_subquery_and_cte_are_filtered_too(engine: tuple[str, Any]) -> None:
    dialect, eng = engine
    sql = (
        "WITH t AS (SELECT id, total FROM sg_invoices) "
        "SELECT COUNT(*) FROM t WHERE total > (SELECT MIN(total) FROM sg_invoices)"
    )
    assert int(_run(dialect, eng, sql)[0][0]) == 2  # min over visible rows is 50
