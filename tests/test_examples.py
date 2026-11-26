"""The integration recipes in examples/, fed fake model output, on SQLite, without network.

The generic and SQLAlchemy recipes always run. The framework recipes are skipped when the
framework isn't installed; the Recipes workflow installs them and sets SQLGUARDLLM_RECIPES=1,
which turns a missing framework into a failure instead of a skip.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sqlguardllm import Guard, GuardViolation

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture(autouse=True)
def _examples_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLES))


def recipe(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"recipe_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def need(module: str) -> ModuleType:
    """Import a framework, or skip (fail when SQLGUARDLLM_RECIPES=1)."""
    if os.environ.get("SQLGUARDLLM_RECIPES") == "1":
        return importlib.import_module(module)
    skipped: ModuleType = pytest.importorskip(module)
    return skipped


@pytest.fixture
def demo() -> ModuleType:
    return recipe("demo_data")


@pytest.fixture
def db_path(demo: ModuleType, tmp_path: Path) -> Iterator[Path]:
    path: Path = demo.create_demo_database(tmp_path / "sales.db")
    yield path
    with closing(sqlite3.connect(path)) as conn:  # nothing was written, whatever happened
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone() == (3,)


def test_generic_wrapper_runs_only_safe_sql(demo: ModuleType, db_path: Path) -> None:
    generic = recipe("generic_wrapper")
    with closing(demo.connect_read_only(db_path)) as conn:
        run_sql = generic.guarded_runner(conn, Guard(demo.POLICY), lambda: demo.CONTEXT)
        assert run_sql(demo.ALLOWED_SQL) == [(350.0,)]  # invoice 12 is in branch 3
        for sql in (demo.HIDDEN_COLUMN_SQL, demo.WRITE_SQL):
            with pytest.raises(GuardViolation):
                run_sql(sql)
        answer = generic.answer_for_model(run_sql, demo.HIDDEN_COLUMN_SQL)
        assert answer == "Query rejected: customers.email is a hidden column"
        assert "ana@example.com" not in answer


def test_generic_wrapper_script(capsys: pytest.CaptureFixture[str]) -> None:
    recipe("generic_wrapper").main()
    out = capsys.readouterr().out
    assert "Rows: [(350.0,)]" in out
    assert out.count("Query rejected") == 2


def test_sqlalchemy_wrapper(demo: ModuleType, db_path: Path) -> None:
    sqlalchemy = need("sqlalchemy")
    wrapper = recipe("sqlalchemy_wrapper")
    engine = sqlalchemy.create_engine(f"sqlite:///{db_path}")
    guard = Guard(demo.POLICY)
    try:
        rows = wrapper.run_sql(engine, guard, demo.ALLOWED_SQL, demo.CONTEXT)
        assert [tuple(row) for row in rows] == [(350.0,)]
        for sql in (demo.HIDDEN_COLUMN_SQL, demo.WRITE_SQL):
            with pytest.raises(GuardViolation):
                wrapper.run_sql(engine, guard, sql, demo.CONTEXT)
    finally:
        engine.dispose()


def test_langchain_agent_repairs_a_rejected_query(demo: ModuleType, db_path: Path) -> None:
    agents = need("langchain.agents")
    messages = need("langchain_core.messages")
    fakes = need("langchain_core.language_models.fake_chat_models")
    lc = recipe("langchain_sql")

    def execute(sql: str) -> list[tuple[Any, ...]]:
        with closing(demo.connect_read_only(db_path)) as conn:
            return list(conn.execute(sql).fetchall())

    def call(i: int, sql: str) -> Any:
        tool_call = {"name": "sql_db_query", "args": {"query": sql}, "id": f"call_{i}"}
        return messages.AIMessage(content="", tool_calls=[tool_call])

    # A model that first asks for an email, then fixes its query after the error.
    script = [
        call(1, demo.HIDDEN_COLUMN_SQL),
        call(2, demo.ALLOWED_SQL),
        messages.AIMessage(content="Your branches made 350.0."),
    ]
    tool_calling_fake = type(
        "ToolCallingFake",
        (fakes.FakeMessagesListChatModel,),
        {"bind_tools": lambda self, tools, **kwargs: self},
    )
    tool = lc.sql_query_tool(Guard(demo.POLICY), execute, lambda: demo.CONTEXT)
    agent = agents.create_agent(tool_calling_fake(responses=script), [tool])
    state = agent.invoke({"messages": [messages.HumanMessage(content="Revenue and emails?")]})

    results = [m.content for m in state["messages"] if isinstance(m, messages.ToolMessage)]
    assert results == ["Error: customers.email is a hidden column", "[(350.0,)]"]
    assert state["messages"][-1].content == "Your branches made 350.0."


def test_langchain_community_toolkit(demo: ModuleType, db_path: Path) -> None:
    tools = need("langchain_community.tools")
    lcc = recipe("langchain_community_sql")
    db = lcc.GuardedSQLDatabase.from_uri(
        f"sqlite:///{db_path}",
        guard=Guard(demo.POLICY),
        context_fn=lambda: demo.CONTEXT,
        include_tables=sorted(demo.POLICY.tables),
        sample_rows_in_table_info=0,
    )
    query = tools.QuerySQLDatabaseTool(db=db)
    assert query.invoke({"query": demo.ALLOWED_SQL}) == "[(350.0,)]"
    assert query.invoke({"query": demo.HIDDEN_COLUMN_SQL}).startswith("Error: customers.email")
    assert query.invoke({"query": demo.WRITE_SQL}).startswith("Error: ")
    with pytest.raises(GuardViolation):
        db.run(demo.WRITE_SQL)
    assert "ana@example.com" not in db.get_table_info()


def test_llamaindex_query_engine(demo: ModuleType, db_path: Path) -> None:
    core = need("llama_index.core")
    llms = need("llama_index.core.llms")
    embeddings = need("llama_index.core.embeddings")
    engines = need("llama_index.core.query_engine")
    sqlalchemy = need("sqlalchemy")
    li = recipe("llamaindex_sql")

    def fake_llm(sql: str) -> Any:
        """An LLM that always writes ``sql``, in the format LlamaIndex parses."""
        methods = {
            "metadata": property(lambda self: llms.LLMMetadata()),
            "complete": lambda self, prompt, formatted=False, **kw: llms.CompletionResponse(
                text=f"SQLQuery: {sql}\nSQLResult:"
            ),
            "stream_complete": lambda self, prompt, formatted=False, **kw: iter(()),
        }
        return type("FakeSQLModel", (llms.CustomLLM,), methods)()

    engine = sqlalchemy.create_engine(f"sqlite:///{db_path}")
    db = li.GuardedSQLDatabase(
        engine,
        guard=Guard(demo.POLICY),
        context_fn=lambda: demo.CONTEXT,
        include_tables=sorted(demo.POLICY.tables),
        sample_rows_in_table_info=0,
    )
    assert isinstance(db, core.SQLDatabase)
    try:
        answers = {}
        for sql in (demo.ALLOWED_SQL, demo.HIDDEN_COLUMN_SQL, demo.WRITE_SQL):
            query_engine = engines.NLSQLTableQueryEngine(
                sql_database=db,
                tables=sorted(demo.POLICY.tables),
                llm=fake_llm(sql),
                embed_model=embeddings.MockEmbedding(embed_dim=8),  # no OpenAI default
                synthesize_response=False,
            )
            answers[sql] = str(query_engine.query("How much did my branches sell?"))
        assert answers[demo.ALLOWED_SQL] == "[(350.0,)]"
        assert answers[demo.HIDDEN_COLUMN_SQL] == "Error: customers.email is a hidden column"
        assert answers[demo.WRITE_SQL].startswith("Error: ")
    finally:
        engine.dispose()


def test_vanna_runner(demo: ModuleType, db_path: Path, tmp_path: Path) -> None:
    vanna = need("vanna")
    sqlite_runner = need("vanna.integrations.sqlite")
    local = need("vanna.integrations.local")
    memory = need("vanna.integrations.local.agent_memory")
    vanna_tools = need("vanna.tools")
    vr = recipe("vanna_runner")

    runner = vr.GuardedSqlRunner(
        sqlite_runner.SqliteRunner(database_path=str(db_path)), Guard(demo.POLICY)
    )
    registry = vanna.ToolRegistry()
    registry.register_local_tool(
        vanna_tools.RunSqlTool(sql_runner=runner, file_system=local.LocalFileSystem(tmp_path)),
        access_groups=["user"],
    )
    user = vanna.User(id="ana", group_memberships=["user"], metadata={"branches": [1, 2]})
    context = vanna.ToolContext(
        user=user, conversation_id="t", request_id="t", agent_memory=memory.DemoAgentMemory()
    )

    async def run(sql: str) -> Any:
        call = vanna.ToolCall(id="1", name="run_sql", arguments={"sql": sql})
        return await registry.execute(call, context)

    allowed = asyncio.run(run(demo.ALLOWED_SQL))
    assert allowed.success, allowed.error
    assert allowed.metadata["results"] == [{"revenue": 350.0}]
    for sql in (demo.HIDDEN_COLUMN_SQL, demo.WRITE_SQL):
        rejected = asyncio.run(run(sql))
        assert not rejected.success
        assert rejected.result_for_llm.startswith("Error executing query: ")
    assert "hidden column" in asyncio.run(run(demo.HIDDEN_COLUMN_SQL)).result_for_llm
