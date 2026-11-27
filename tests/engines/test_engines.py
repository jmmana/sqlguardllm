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


@pytest.mark.parametrize("engine", ["tsql"], indirect=True)
@pytest.mark.parametrize("order_by", ["1", "invoice_id"])
@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("UNION", "id >= 2", "id <= 2", [3, 2]),
        ("UNION ALL", "id >= 2", "id <> 2", [3, 3]),
        ("INTERSECT", "id > 0", "id <> 0", [3, 2]),
        ("EXCEPT", "id > 0", "id = 4", [3, 2]),
    ],
)
def test_tsql_ordered_set_limit(
    engine: tuple[str, Any],
    *,
    order_by: str,
    operator: str,
    left: str,
    right: str,
    expected: list[int],
) -> None:
    dialect, eng = engine
    sql = (
        f"SELECT id AS invoice_id FROM sg_invoices WHERE {left} "
        f"{operator} SELECT id FROM sg_invoices WHERE {right} ORDER BY {order_by} DESC"
    )
    assert _run(dialect, eng, sql, max_rows=2) == [(ident,) for ident in expected]


@pytest.mark.parametrize("engine", ["tsql"], indirect=True)
@pytest.mark.parametrize(
    ("suffix", "max_rows"),
    [
        ("", 1),
        (" FETCH NEXT 1 ROWS ONLY", 2),
        (" FETCH NEXT 5 ROWS ONLY", 1),
    ],
)
def test_tsql_ordered_union_preserves_paging(
    engine: tuple[str, Any], suffix: str, max_rows: int
) -> None:
    dialect, eng = engine
    sql = (
        "SELECT id FROM sg_invoices UNION SELECT id FROM sg_invoices "
        f"ORDER BY id DESC OFFSET 1 ROWS{suffix}"
    )
    assert _run(dialect, eng, sql, max_rows=max_rows) == [(2,)]


@pytest.mark.parametrize("engine", ["tsql"], indirect=True)
def test_tsql_ordered_union_keeps_branch_top(engine: tuple[str, Any]) -> None:
    dialect, eng = engine
    sql = (
        "SELECT TOP 1 1 AS invoice_id FROM sg_invoices WHERE id <= 2 "
        "UNION ALL SELECT TOP 1 3 FROM sg_invoices WHERE id >= 2 ORDER BY invoice_id DESC"
    )
    assert _run(dialect, eng, sql, max_rows=3) == [(3,), (1,)]


@pytest.mark.parametrize("engine", ["tsql"], indirect=True)
def test_tsql_ordered_union_keeps_zero_limit_from_postgres(engine: tuple[str, Any]) -> None:
    _, eng = engine
    sql = "SELECT id FROM sg_invoices UNION SELECT id FROM sg_invoices ORDER BY id LIMIT 0 OFFSET 1"
    assert _run("postgres", eng, sql, output_dialect="tsql", max_rows=2) == []
