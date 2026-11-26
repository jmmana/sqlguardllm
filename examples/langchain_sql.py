"""Recipe: a LangChain 1.x SQL tool that only runs SQL sqlguardllm has made safe.

LangChain's current SQL agent tutorial writes its own ``sql_db_query`` tool and hands it to
``create_agent``; this is that tool with the guard in front. Targets langchain-core 1.x
(``langchain>=1.0`` for ``create_agent``). Needs ``pip install "langchain-core>=1.0,<2"``.
Run it::

    python examples/langchain_sql.py

With a real model::

    from langchain.agents import create_agent

    agent = create_agent(model, [sql_query_tool(guard, execute, current_user_context)])
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from demo_data import (
    ALLOWED_SQL,
    CONTEXT,
    HIDDEN_COLUMN_SQL,
    POLICY,
    WRITE_SQL,
    connect_read_only,
    create_demo_database,
)
from langchain_core.tools import BaseTool, tool

from sqlguardllm import Guard, GuardViolation


def sql_query_tool(
    guard: Guard,
    execute: Callable[[str], list[tuple[Any, ...]]],
    context_fn: Callable[[], Mapping[str, Any]],
) -> BaseTool:
    """The ``sql_db_query`` tool, guarded. ``execute`` runs SQL on a read-only connection."""

    @tool
    def sql_db_query(query: str) -> str:
        """Run a read-only SQL query and return the rows. If the query is rejected, the
        error says why: rewrite the query and try again."""
        try:
            safe_sql = guard.enforce(query, context_fn())
        except GuardViolation as exc:
            return f"Error: {exc}"  # reasons the model can act on, without hidden data
        return repr(execute(safe_sql))

    return sql_db_query


def main() -> None:
    guard = Guard(POLICY)
    with tempfile.TemporaryDirectory() as tmp:
        path = create_demo_database(Path(tmp) / "sales.db")

        def execute(sql: str) -> list[tuple[Any, ...]]:
            # A connection per call: agents may run tools in worker threads.
            with closing(connect_read_only(path)) as conn:
                return conn.execute(sql).fetchall()

        query_tool = sql_query_tool(guard, execute, lambda: CONTEXT)
        for sql in (ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL):
            print(f"{sql}\n  -> {query_tool.invoke({'query': sql})}")


if __name__ == "__main__":
    main()
