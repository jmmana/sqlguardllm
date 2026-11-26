"""The MCP server, called in-process through an MCP client against a temporary SQLite database."""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from sqlalchemy import create_engine

from sqlguardllm import Policy
from sqlguardllm.cli import main
from sqlguardllm.mcp_server import _begin_read_only, _jsonable, create_server

CONTEXT: dict[str, Any] = {"user": {"branches": [1], "token": "ctx-secret-7f3a"}}
SECRETS = ("ctx-secret-7f3a", "hunter2")

SCHEMA = """
CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    customer_id INTEGER REFERENCES customers(id),
    total NUMERIC,
    note TEXT
);
CREATE TABLE salaries (id INTEGER PRIMARY KEY, department TEXT, amount INTEGER);
CREATE TABLE visits (id INTEGER PRIMARY KEY, day TEXT);
CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT);
INSERT INTO branches VALUES (1, 'North'), (2, 'South');
INSERT INTO customers VALUES
    (1, 'Ana', 'ana@example.com', '555-0101'),
    (2, 'Luis', 'luis@example.com', '555-0102');
INSERT INTO invoices VALUES
    (1, 1, 1, 10.5, '10% off'),
    (2, 1, 2, 20, NULL),
    (3, 1, 1, 30, NULL),
    (4, 2, 2, 40, NULL),
    (5, 2, 1, 50, NULL);
INSERT INTO salaries VALUES (1, 'sales', 100), (2, 'sales', 200), (3, 'it', 300);
INSERT INTO visits VALUES (1, '2026-11-01');
INSERT INTO secrets VALUES (1, 'the launch codes');
"""


def make_policy(**overrides: Any) -> Policy:
    values: dict[str, Any] = {
        "dialect": "sqlite",
        "tables": {"branches", "customers", "invoices", "salaries", "visits", "ghost"},
        "hidden_columns": {"customers.email"},
        "masked_columns": {"customers.phone": "redact"},
        "row_filters": {"invoices": "branch_id IN ({user.branches})"},
        "required_filters": {"visits": ["day"]},
        "aggregate_only": {"salaries": 2},
        "max_rows": 100,
    }
    values.update(overrides)
    return Policy(**values)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    conn.close()
    return path


@pytest.fixture
def url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


@pytest.fixture
def server(url: str) -> MCPServer[Any]:
    return create_server(make_policy(), url, context=CONTEXT)


def call(server: MCPServer[Any], name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call a tool through an in-process MCP client and return the parsed result."""

    async def go() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    result = anyio.run(go)
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    assert json.loads(text(result)) == result.structured_content
    return result.structured_content


def call_error(server: MCPServer[Any], name: str, arguments: dict[str, Any]) -> str:
    """Call a tool that must fail and return its error message, without the SDK's prefix."""

    async def go() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    result = anyio.run(go)
    assert result.is_error, text(result)
    prefix = f"Error executing tool {name}: "
    assert text(result).startswith(prefix)
    return text(result).removeprefix(prefix)


def text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def count_invoices(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()
    conn.close()
    return int(count)


# Tools and metadata ---------------------------------------------------------------------------


def test_lists_four_read_only_tools(server: MCPServer[Any]) -> None:
    async def go() -> Any:
        async with Client(server) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in anyio.run(go).tools}
    assert set(tools) == {"list_tables", "describe_table", "explain_query", "query"}
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
    assert "sqlite" in (tools["query"].description or "")
    assert tools["query"].input_schema["required"] == ["sql"]


def test_list_tables_shows_allowed_tables_and_visible_columns(server: MCPServer[Any]) -> None:
    data = call(server, "list_tables")
    assert data["dialect"] == "sqlite"
    assert data["max_rows"] == 100
    tables = {item["name"]: item for item in data["tables"]}
    assert set(tables) == {"branches", "customers", "invoices", "salaries", "visits"}
    assert tables["customers"]["columns"] == ["id", "name", "phone"]
    assert tables["customers"]["masked_columns"] == {"phone": "redact"}
    assert tables["salaries"]["aggregate_only"] == 2
    assert tables["visits"]["required_filters"] == ["day"]
    assert "masked_columns" not in tables["branches"]


def test_describe_table(server: MCPServer[Any]) -> None:
    data = call(server, "describe_table", {"table": "invoices"})
    assert data["table"] == "invoices"
    columns = {column["name"]: column for column in data["columns"]}
    assert columns["id"]["primary_key"] is True
    assert columns["branch_id"]["nullable"] is False
    assert columns["total"]["type"] == "NUMERIC"
    assert data["rows_filtered_for_user"] is True
    assert data["select_star_allowed"] is True
    assert data["aggregate_only"] is None
    references = {key["references"]: key for key in data["foreign_keys"]}
    assert references["branches"] == {
        "columns": ["branch_id"],
        "references": "branches",
        "referred_columns": ["id"],
    }
    assert set(references) == {"branches", "customers"}


def test_describe_table_marks_hidden_masked_and_aggregate_only(server: MCPServer[Any]) -> None:
    customers = call(server, "describe_table", {"table": "customers"})
    assert [column["name"] for column in customers["columns"]] == ["id", "name", "phone"]
    assert customers["columns"][2]["masked"] == "redact"
    assert "masked" not in customers["columns"][1]
    assert customers["select_star_allowed"] is False
    assert customers["rows_filtered_for_user"] is False

    salaries = call(server, "describe_table", {"table": "SALARIES"})
    assert salaries["table"] == "salaries"
    assert salaries["aggregate_only"] == 2
    assert salaries["select_star_allowed"] is False

    visits = call(server, "describe_table", {"table": "visits"})
    assert visits["required_filters"] == ["day"]


def test_describe_table_refuses_tables_outside_the_policy(server: MCPServer[Any]) -> None:
    message = call_error(server, "describe_table", {"table": "secrets"})
    assert message == "table_not_allowed: secrets is not an allowed table"
    assert "launch" not in message
    assert "not found" in call_error(server, "describe_table", {"table": "ghost"})
    assert "not a valid table name" in call_error(server, "describe_table", {"table": "a.b.c.d"})


# query -----------------------------------------------------------------------------------------


def test_query_returns_only_the_users_rows(server: MCPServer[Any]) -> None:
    data = call(server, "query", {"sql": "SELECT id, branch_id, total FROM invoices ORDER BY id"})
    assert data["columns"] == ["id", "branch_id", "total"]
    assert data["rows"] == [[1, 1, 10.5], [2, 1, 20], [3, 1, 30]]
    assert data["row_count"] == 3
    assert data["limit_reached"] is False


def test_row_filter_survives_joins_and_subqueries(server: MCPServer[Any]) -> None:
    sql = (
        "SELECT b.name, COUNT(*) AS n FROM branches b "
        "JOIN (SELECT branch_id FROM invoices) i ON i.branch_id = b.id GROUP BY b.name"
    )
    assert call(server, "query", {"sql": sql})["rows"] == [["North", 3]]


def test_percent_signs_reach_the_database(server: MCPServer[Any]) -> None:
    data = call(server, "query", {"sql": "SELECT id FROM invoices WHERE note LIKE '%\\% off%'"})
    assert data["rows"] == []
    data = call(server, "query", {"sql": "SELECT id FROM invoices WHERE note LIKE '10%'"})
    assert data["rows"] == [[1]]


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM invoices", "not_a_query"),
        ("UPDATE invoices SET total = 0", "not_a_query"),
        ("DROP TABLE invoices", "not_a_query"),
        ("WITH d AS (DELETE FROM invoices RETURNING *) SELECT * FROM d", "write_operation"),
        ("SELECT 1; DELETE FROM invoices", "multiple_statements"),
    ],
)
def test_writes_are_rejected_and_nothing_changes(
    server: MCPServer[Any], db_path: Path, sql: str, code: str
) -> None:
    message = call_error(server, "query", {"sql": sql})
    assert message.startswith(f"{code}: ")
    assert count_invoices(db_path) == 5


def test_hidden_column_is_rejected_with_a_message_the_model_can_use(
    server: MCPServer[Any],
) -> None:
    message = call_error(server, "query", {"sql": "SELECT name, email FROM customers"})
    assert message == "hidden_column: customers.email is a hidden column"
    assert "@example.com" not in message
    assert call_error(server, "query", {"sql": "SELECT * FROM customers"}).startswith(
        "hidden_column:"
    )


def test_masked_column_comes_back_masked(server: MCPServer[Any]) -> None:
    data = call(server, "query", {"sql": "SELECT name, phone FROM customers ORDER BY id"})
    assert data["rows"] == [["Ana", "***"], ["Luis", "***"]]


def test_tables_outside_the_policy_are_rejected(server: MCPServer[Any]) -> None:
    message = call_error(server, "query", {"sql": "SELECT value FROM secrets"})
    assert message == "table_not_allowed: secrets is not an allowed table"


def test_aggregate_only_table(server: MCPServer[Any]) -> None:
    assert call_error(server, "query", {"sql": "SELECT amount FROM salaries"}).startswith(
        "aggregate_required:"
    )
    sql = "SELECT department, SUM(amount) AS total FROM salaries GROUP BY department"
    assert call(server, "query", {"sql": sql})["rows"] == [["sales", 300]]


def test_required_filter(server: MCPServer[Any]) -> None:
    assert call_error(server, "query", {"sql": "SELECT id FROM visits"}).startswith(
        "missing_required_filter:"
    )
    sql = "SELECT id FROM visits WHERE day >= '2026-11-01'"
    assert call(server, "query", {"sql": sql})["rows"] == [[1]]


def test_several_violations_come_back_one_per_line(server: MCPServer[Any]) -> None:
    message = call_error(
        server, "query", {"sql": "SELECT email, s.value FROM customers, secrets s"}
    )
    codes = {line.split(":")[0] for line in message.splitlines()}
    assert codes == {"hidden_column", "table_not_allowed"}


def test_at_most_max_rows(url: str) -> None:
    server = create_server(make_policy(max_rows=2), url, context=CONTEXT)
    data = call(
        server, "query", {"sql": "SELECT id FROM branches UNION ALL SELECT id FROM branches"}
    )
    assert data["row_count"] == 2
    assert data["limit_reached"] is True


def test_without_max_rows_the_server_still_caps_rows(url: str) -> None:
    server = create_server(make_policy(max_rows=None), url, context=CONTEXT)
    sql = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 5000) "
        "SELECT x FROM c"
    )
    data = call(server, "query", {"sql": sql})
    assert data["row_count"] == 1000
    assert data["limit_reached"] is True


def test_missing_context_rejects_the_query(url: str) -> None:
    server = create_server(make_policy(), url)
    message = call_error(server, "query", {"sql": "SELECT id FROM invoices"})
    assert message.startswith("missing_context:")


def test_database_errors_are_short_and_do_not_echo_the_sql(server: MCPServer[Any]) -> None:
    message = call_error(server, "query", {"sql": "SELECT nope FROM invoices"})
    assert message == "The database rejected the query: no such column: nope"


def test_slow_query_is_cancelled(url: str) -> None:
    server = create_server(make_policy(), url, context=CONTEXT, timeout=0.3)
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    message = call_error(server, "query", {"sql": sql})
    assert message == "The query took longer than 0.3 s and was cancelled"
    # The connection is usable afterwards: the progress handler was removed.
    assert call(server, "query", {"sql": "SELECT COUNT(*) AS n FROM branches"})["rows"] == [[2]]


def test_messages_in_spanish(url: str) -> None:
    server = create_server(make_policy(), url, context=CONTEXT, language="es")
    message = call_error(server, "query", {"sql": "SELECT value FROM secrets"})
    assert message == "table_not_allowed: secrets no es una tabla permitida"
    message = call_error(server, "describe_table", {"table": "ghost"})
    assert message == "ghost no existe en la base de datos"


def test_connection_is_read_only_even_without_the_guard(url: str) -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        transaction = conn.begin()
        restore = _begin_read_only(conn, 5)
        with pytest.raises(Exception, match="readonly"):
            conn.exec_driver_sql("DELETE FROM invoices")
        restore()
        transaction.rollback()
    engine.dispose()


# explain_query -------------------------------------------------------------------------------


def test_explain_query_shows_the_sql_that_would_run(server: MCPServer[Any]) -> None:
    sql = "SELECT i.id, c.name, c.phone FROM invoices i JOIN customers c ON c.id = i.customer_id"
    data = call(server, "explain_query", {"sql": sql})
    assert data["ok"] is True
    assert "branch_id IN (1)" in data["sql"]
    assert data["sql"].endswith("LIMIT 100")
    assert data["tables"] == ["customers", "invoices"]
    assert data["columns"] == {
        "customers": ["id", "name", "phone"],
        "invoices": ["customer_id", "id"],
    }
    assert data["masked_columns"] == {"customers.phone": "redact"}
    assert data["row_filters"] == {"invoices": "branch_id IN (1)"}
    assert data["limit"] == 100
    assert data["violations"] == []
    assert "text" not in data


def test_explain_query_shows_minimum_group_sizes(server: MCPServer[Any]) -> None:
    sql = "SELECT department, AVG(amount) AS average FROM salaries GROUP BY department"
    data = call(server, "explain_query", {"sql": sql})
    assert data["ok"] is True
    assert data["aggregate_only"] == {"salaries": 2}
    assert "HAVING COUNT(*) >= 2" in data["sql"]


def test_explain_query_reports_violations_without_failing(server: MCPServer[Any]) -> None:
    data = call(server, "explain_query", {"sql": "DELETE FROM invoices"})
    assert data["ok"] is False
    assert data["sql"] is None
    assert [item["code"] for item in data["violations"]] == ["not_a_query"]
    data = call(server, "explain_query", {"sql": "SELECT email FROM customers"})
    assert data["ok"] is False
    assert data["tables"] == ["customers"]
    assert data["violations"][0]["message"] == "customers.email is a hidden column"


# Secrets never leak ------------------------------------------------------------------------------


def test_context_and_url_never_appear_in_tool_output_or_logs(
    url: str, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = create_server(make_policy(), url, context=CONTEXT)

    async def go() -> list[str]:
        outputs: list[str] = []
        async with Client(server) as client:
            listing = await client.list_tools()
            outputs.append(listing.model_dump_json())
            calls = [
                ("list_tables", {}),
                ("describe_table", {"table": "ghost"}),
                ("describe_table", {"table": "invoices"}),
                ("describe_table", {"table": "secrets"}),
                ("explain_query", {"sql": "SELECT id FROM invoices"}),
                ("explain_query", {"sql": "SELECT {user.token}"}),
                ("query", {"sql": "SELECT id, note FROM invoices"}),
                ("query", {"sql": "SELECT email FROM customers"}),
                ("query", {"sql": "SELECT nope FROM invoices"}),
            ]
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                outputs.append(result.model_dump_json())
        return outputs

    joined = "\n".join(anyio.run(go))
    assert "Error executing tool" in joined  # the error paths ran
    assert "ghost" in caplog.text  # and logged
    for secret in (*SECRETS, str(db_path), "demo.db"):
        assert secret not in joined
        assert secret not in caplog.text


def test_instructions_name_the_dialect_and_limit(server: MCPServer[Any]) -> None:
    instructions = server.instructions or ""
    assert "Write sqlite SQL" in instructions
    assert "100 per query" in instructions
    for secret in SECRETS:
        assert secret not in instructions


# Values -----------------------------------------------------------------------------------------


def test_values_are_json_friendly() -> None:
    raw = {
        "decimal_int": Decimal("42"),
        "decimal": Decimal("10.25"),
        "decimal_precise": Decimal("0.1000000000000000000001"),
        "nan": float("nan"),
        "date": date(2026, 11, 25),
        "datetime": datetime(2026, 11, 25, 8, 30),
        "bytes": b"\x00\xff",
        "uuid": uuid.UUID(int=1),
        "list": [Decimal("1.5"), None],
        "other": object,
    }
    value = _jsonable(raw)
    assert value["decimal_int"] == 42
    assert value["decimal"] == 10.25
    assert value["decimal_precise"] == "0.1000000000000000000001"
    assert value["nan"] == "nan"
    assert value["date"] == "2026-11-25"
    assert value["datetime"] == "2026-11-25T08:30:00"
    assert value["bytes"] == "AP8="
    assert value["uuid"] == "00000000-0000-0000-0000-000000000001"
    assert value["list"] == [1.5, None]
    assert isinstance(value["other"], str)
    json.dumps(value)


# create_server ------------------------------------------------------------------------------


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(url: str, timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        create_server(make_policy(), url, timeout=timeout)


@pytest.mark.parametrize(
    ("database", "message"),
    [
        ("not a url", "not valid"),
        ("nosuchdb://reader:hunter2@localhost/db", "doesn't know"),
        ("postgresql+nosuchdriver://reader:hunter2@localhost/db", "doesn't know"),
    ],
)
def test_bad_database_urls_never_echo_the_url(database: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as info:
        create_server(make_policy(), database)
    assert "hunter2" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_accepts_an_engine(url: str) -> None:
    engine = create_engine(url)
    server = create_server(make_policy(), engine, context=CONTEXT)
    assert call(server, "query", {"sql": "SELECT COUNT(*) AS n FROM invoices"})["rows"] == [[3]]
    engine.dispose()


# Command line -----------------------------------------------------------------------------------

POLICY_JSON = {
    "dialect": "sqlite",
    "tables": ["branches", "invoices"],
    "row_filters": {"invoices": "branch_id IN ({user.branches})"},
}


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(POLICY_JSON))
    return path


def run_main(*args: str) -> tuple[int, str]:
    stderr = io.StringIO()
    code = main(list(args), stdout=io.StringIO(), stderr=stderr)
    return code, stderr.getvalue()


def test_cli_without_the_extra_exits_2(
    monkeypatch: pytest.MonkeyPatch, policy_file: Path, url: str
) -> None:
    monkeypatch.delitem(sys.modules, "sqlguardllm.mcp_server")
    for name in list(sys.modules):  # as if the SDK had never been installed
        if name == "mcp" or name.startswith("mcp."):
            monkeypatch.setitem(sys.modules, name, None)
    code, stderr = run_main("mcp", "--policy", str(policy_file), "--db", url)
    assert code == 2
    assert 'pip install "sqlguardllm[mcp]"' in stderr
    assert "missing module mcp" in stderr


def test_cli_starts_the_server_over_stdio(
    monkeypatch: pytest.MonkeyPatch, policy_file: Path, url: str, tmp_path: Path
) -> None:
    started: list[tuple[MCPServer[Any], str]] = []
    monkeypatch.setattr(
        MCPServer, "run", lambda self, transport="stdio": started.append((self, transport))
    )
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"user": {"branches": [2]}}))
    code, stderr = run_main(
        "mcp", "--policy", str(policy_file), "--db", url, "--context", f"@{context}"
    )
    assert code == 0, stderr
    ((server, transport),) = started
    assert transport == "stdio"
    data = call(server, "query", {"sql": "SELECT id FROM invoices ORDER BY id"})
    assert data["rows"] == [[4], [5]]


def test_cli_role_from_a_policy_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    started: list[MCPServer[Any]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio": started.append(self))
    roles = tmp_path / "roles.json"
    roles.write_text(
        json.dumps({"base": POLICY_JSON, "roles": {"auditor": {"row_filters": {}, "max_rows": 4}}})
    )
    code, stderr = run_main("mcp", "--policy", str(roles), "--role", "auditor", "--db", url)
    assert code == 0, stderr
    data = call(started[0], "query", {"sql": "SELECT id FROM invoices"})
    assert data["rows"] == [[1], [2], [3], [4]]  # every branch, at most 4 rows

    code, stderr = run_main("mcp", "--policy", str(roles), "--role", "owner", "--db", url)
    assert code == 2
    assert "unknown role 'owner'" in stderr


def test_cli_reads_the_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, policy_file: Path, url: str
) -> None:
    started: list[MCPServer[Any]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio": started.append(self))
    monkeypatch.setenv("DATABASE_URL", url)
    code, stderr = run_main("mcp", "--policy", str(policy_file))
    assert code == 0, stderr
    assert len(started) == 1


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([], "no database"),
        (["--db", "postgresql://reader:hunter2@localhost/db"], "driver is not installed"),
        (["--db", "nosuchdb://reader:hunter2@localhost/db"], "doesn't know"),
        (["--db", "sqlite://", "--timeout", "0"], "--timeout"),
        (["--db", "sqlite://", "--context", "[1]"], "JSON object"),
    ],
)
def test_cli_errors_exit_2_without_echoing_the_url(
    monkeypatch: pytest.MonkeyPatch, policy_file: Path, extra: list[str], message: str
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, stderr = run_main("mcp", "--policy", str(policy_file), *extra)
    assert code == 2
    assert message in stderr
    assert "hunter2" not in stderr


def test_cli_invalid_policy_exits_2(tmp_path: Path, url: str) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"dialect": "klingon", "tables": ["t"]}))
    code, stderr = run_main("mcp", "--policy", str(bad), "--db", url)
    assert code == 2
    assert "dialect" in stderr


def test_importing_sqlguardllm_does_not_need_the_extra() -> None:
    code = (
        "import sys, sqlguardllm, sqlguardllm.cli; "
        "loaded = {'mcp', 'sqlalchemy', 'pydantic'} & set(sys.modules); "
        "print(sorted(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_stdio_end_to_end(policy_file: Path, url: str, tmp_path: Path) -> None:
    """The real command, launched by an MCP client over stdio, as Claude Desktop would."""
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"user": {"branches": [2]}}))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sqlguardllm", "mcp", "--policy", str(policy_file), "--context", f"@{context}"],
        env={**os.environ, "DATABASE_URL": url},
    )

    async def go() -> tuple[set[str], CallToolResult, CallToolResult]:
        with anyio.fail_after(60):
            async with Client(params) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                rows = await client.call_tool("query", {"sql": "SELECT id FROM invoices"})
                rejected = await client.call_tool("query", {"sql": "DELETE FROM invoices"})
                return tools, rows, rejected

    tools, rows, rejected = anyio.run(go)
    assert tools == {"list_tables", "describe_table", "explain_query", "query"}
    assert rows.structured_content is not None
    assert rows.structured_content["rows"] == [[4], [5]]
    assert rejected.is_error
    assert "not_a_query" in text(rejected)


# The walkthrough in docs/MCP.md ---------------------------------------------------------------

DEMO = Path(__file__).parents[1] / "examples" / "mcp"


@pytest.fixture
def demo_url(tmp_path: Path) -> str:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as conn:  # what the walkthrough's one-liner does
        conn.executescript((DEMO / "demo.sql").read_text(encoding="utf-8"))
    conn.close()
    return f"sqlite:///{path}"


def demo_server(monkeypatch: pytest.MonkeyPatch, demo_url: str, role: str) -> MCPServer[Any]:
    started: list[MCPServer[Any]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio": started.append(self))
    code, stderr = run_main(
        "mcp",
        "--policy",
        str(DEMO / "roles.yaml"),
        "--role",
        role,
        "--context",
        f"@{DEMO / 'context.json'}",
        "--db",
        demo_url,
    )
    assert code == 0, stderr
    return started[0]


def test_demo_example_validates_against_the_policy_set_schema() -> None:
    import jsonschema  # noqa: PLC0415
    import yaml  # noqa: PLC0415

    from sqlguardllm.schema import policy_set_schema  # noqa: PLC0415

    data = yaml.safe_load((DEMO / "roles.yaml").read_text(encoding="utf-8"))
    jsonschema.validate(data, policy_set_schema())


def test_demo_walkthrough_as_a_branch_manager(
    monkeypatch: pytest.MonkeyPatch, demo_url: str
) -> None:
    server = demo_server(monkeypatch, demo_url, "manager")
    names = [table["name"] for table in call(server, "list_tables")["tables"]]
    assert names == ["branches", "customers", "invoices"]

    sql = (
        "SELECT b.name, COUNT(*) AS invoices, SUM(i.total) AS total "
        "FROM invoices i JOIN branches b ON b.id = i.branch_id GROUP BY b.name"
    )
    assert call(server, "query", {"sql": sql})["rows"] == [["Centro", 3, 243.25]]

    data = call(server, "query", {"sql": "SELECT name, phone FROM customers ORDER BY id"})
    assert data["rows"][0] == ["Ana Ruiz", "+5***"]

    assert call_error(server, "query", {"sql": "SELECT secret FROM api_keys"}).startswith(
        "table_not_allowed:"
    )
    assert call_error(server, "query", {"sql": "SELECT email FROM customers"}).startswith(
        "hidden_column:"
    )
    assert call_error(server, "query", {"sql": "DELETE FROM invoices"}).startswith("not_a_query:")


def test_demo_walkthrough_as_an_auditor(monkeypatch: pytest.MonkeyPatch, demo_url: str) -> None:
    server = demo_server(monkeypatch, demo_url, "auditor")
    data = call(server, "query", {"sql": "SELECT COUNT(*) AS n FROM invoices"})
    assert data["rows"] == [[6]]
    assert call_error(server, "query", {"sql": "SELECT name FROM customers"}).startswith(
        "hidden_column:"
    )
