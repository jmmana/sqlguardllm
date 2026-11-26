"""Cost guard: plan parsing on recorded EXPLAIN output, and the MCP flow with a fake estimator."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from sqlalchemy import Connection, create_engine

from sqlguardllm import Policy
from sqlguardllm._cost import CostEstimate, parse_plan_cost
from sqlguardllm.mcp_server import create_server

DATA = Path(__file__).parent / "data"


def _plan(name: str) -> str:
    return (DATA / name).read_text()


def test_parse_postgres16() -> None:
    est = parse_plan_cost("postgresql", _plan("plan_postgres16.json"))
    assert est == CostEstimate(17.5, 143.0)


def test_parse_mysql8_scan_and_join() -> None:
    assert parse_plan_cost("mysql", _plan("plan_mysql8_scan.json")) == CostEstimate(0.55, 3.0)
    assert parse_plan_cost("mysql", _plan("plan_mysql8_join.json")) == CostEstimate(1.7, 3.0)


@pytest.mark.parametrize(
    ("engine", "text"),
    [
        ("postgresql", "nope"),
        ("postgresql", "[]"),
        ("postgresql", '[{"Plan": {}}]'),
        ("postgresql", '[{"Plan": {"Total Cost": "x"}}]'),
        ("mysql", "{}"),
        ("mysql", '{"query_block": {"cost_info": {"query_cost": "abc"}}}'),
        ("sqlite", "[]"),
    ],
)
def test_parse_rejects_garbage(engine: str, text: str) -> None:
    with pytest.raises(ValueError):
        parse_plan_cost(engine, text)


def test_mysql_no_tables_costs_nothing() -> None:
    text = '{"query_block": {"select_id": 1, "message": "No tables used"}}'
    assert parse_plan_cost("mysql", text) == CostEstimate(0.0, 0.0)


POLICY = Policy.from_dict({"dialect": "sqlite", "tables": ["items"], "max_rows": 10})
PG_POLICY = Policy.from_dict({"dialect": "postgres", "tables": ["items"], "max_rows": 10})


def _server(tmp_path: Path, **options: Any) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE items (id INTEGER, name TEXT)")
        conn.exec_driver_sql("INSERT INTO items VALUES (1, 'a')")
    return create_server(POLICY, engine, **options)


async def _call(server: Any, tool: str, **args: Any) -> tuple[bool, Any]:
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
    if result.is_error:
        return True, result.content[0].text  # type: ignore[union-attr]
    return False, result.structured_content or {}


def _fake(cost: float, rows: float | None = 5) -> Any:
    calls: list[str] = []

    def estimator(conn: Connection, sql: str) -> CostEstimate:
        calls.append(sql)
        return CostEstimate(cost, rows)

    estimator.calls = calls  # type: ignore[attr-defined]
    return estimator


def test_query_over_max_cost_is_refused_and_not_run(tmp_path: Path) -> None:
    est = _fake(5000)
    server = _server(tmp_path, max_cost=100, estimator=est)
    is_error, text = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert is_error
    assert "5,000" in str(text) and "100" in str(text) and "filter" in str(text)
    assert len(est.calls) == 1


def test_query_under_threshold_runs(tmp_path: Path) -> None:
    server = _server(tmp_path, max_cost=100, max_plan_rows=10, estimator=_fake(5, 3))
    is_error, out = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert not is_error and out["rows"] == [[1]]


def test_max_plan_rows_refuses(tmp_path: Path) -> None:
    server = _server(tmp_path, max_plan_rows=10, estimator=_fake(1, 1e6))
    is_error, text = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert is_error and "rows 1,000,000" in str(text)


def test_fails_closed_when_estimate_breaks(tmp_path: Path) -> None:
    def broken(conn: Connection, sql: str) -> CostEstimate:
        raise ValueError("boom")

    server = _server(tmp_path, max_cost=100, estimator=broken)
    is_error, text = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert is_error and "not run" in str(text)


def test_missing_row_estimate_fails_closed(tmp_path: Path) -> None:
    server = _server(tmp_path, max_plan_rows=10, estimator=_fake(1, None))
    is_error, _ = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert is_error


def test_no_threshold_no_explain(tmp_path: Path) -> None:
    est = _fake(1e9)
    server = _server(tmp_path, estimator=est)
    is_error, _ = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert not is_error and est.calls == []


def test_guard_violation_skips_estimate(tmp_path: Path) -> None:
    est = _fake(1)
    server = _server(tmp_path, max_cost=100, estimator=est)
    is_error, _ = anyio.run(lambda: _call(server, "query", sql="DELETE FROM items"))
    assert is_error and est.calls == []


def test_explain_query_reports_estimate(tmp_path: Path) -> None:
    server = _server(tmp_path, max_cost=100, estimator=_fake(7, 2))
    _, out = anyio.run(lambda: _call(server, "explain_query", sql="SELECT id FROM items"))
    assert out["estimate"] == {"cost": 7.0, "rows": 2.0, "max_cost": 100.0}


def test_unsupported_engine_warns_and_ignores(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        server = _server(tmp_path, max_cost=1)
    assert "cost guard" in caplog.text and "ignored" in caplog.text
    is_error, _ = anyio.run(lambda: _call(server, "query", sql="SELECT id FROM items"))
    assert not is_error


@pytest.mark.parametrize("bad", [0, -1, float("nan"), True])
def test_invalid_threshold(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ValueError):
        _server(tmp_path, max_cost=bad)


@pytest.mark.skipif(not os.environ.get("SG_PG_URL"), reason="needs SG_PG_URL (PostgreSQL)")
def test_postgres_real_explain() -> None:
    url = os.environ["SG_PG_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS items")
        conn.exec_driver_sql("CREATE TABLE items (id int, name text)")
        conn.exec_driver_sql("INSERT INTO items SELECT g, 'x' FROM generate_series(1,5000) g")
        conn.exec_driver_sql("ANALYZE items")
    try:
        cheap = create_server(PG_POLICY, engine, max_cost=1_000_000)
        strict = create_server(PG_POLICY, engine, max_cost=1)
        ok, out = anyio.run(lambda: _call(cheap, "query", sql="SELECT id FROM items"))
        assert not ok and len(out["rows"]) == 10
        bad, text = anyio.run(
            lambda: _call(strict, "query", sql="SELECT id FROM items ORDER BY name")
        )
        assert bad and "too expensive" in str(text)
        _, exp = anyio.run(
            lambda: _call(strict, "explain_query", sql="SELECT id FROM items ORDER BY name")
        )
        assert exp["estimate"]["cost"] > 1
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS items")
