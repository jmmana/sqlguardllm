"""Recipe: a Vanna 2.0 ``SqlRunner`` that only runs SQL sqlguardllm has made safe.

Wraps any runner (``SqliteRunner``, ``PostgresRunner``…) used by ``RunSqlTool``. Row filters
get their values from the Vanna user the ``UserResolver`` authenticated, never from the model.
Targets vanna 2.0. Needs ``pip install "vanna>=2.0,<2.1"``. Run it::

    python examples/vanna_runner.py

With a real model::

    tools = ToolRegistry()
    tools.register_local_tool(
        RunSqlTool(sql_runner=GuardedSqlRunner(SqliteRunner("sales.db"), guard)),
        access_groups=["user"],
    )
    agent = Agent(llm_service=llm, tool_registry=tools, user_resolver=resolver, ...)
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from demo_data import ALLOWED_SQL, HIDDEN_COLUMN_SQL, POLICY, WRITE_SQL, create_demo_database
from vanna import ToolCall, ToolContext, ToolRegistry, User
from vanna.capabilities.sql_runner import RunSqlToolArgs, SqlRunner
from vanna.integrations.local import LocalFileSystem
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.sqlite import SqliteRunner
from vanna.tools import RunSqlTool

from sqlguardllm import Guard


def user_context(context: ToolContext) -> Mapping[str, Any]:
    """Row-filter values from the authenticated user: ``{user.branches}`` → metadata."""
    return {"user": {"id": context.user.id, **context.user.metadata}}


class GuardedSqlRunner(SqlRunner):
    """Checks and rewrites the SQL, then hands the safe SQL to the wrapped runner.

    A rejected query raises ``GuardViolation``; ``RunSqlTool`` returns it to the model as
    ``"Error executing query: <reasons>"``.
    """

    def __init__(
        self,
        runner: SqlRunner,
        guard: Guard,
        context_fn: Callable[[ToolContext], Mapping[str, Any]] = user_context,
    ) -> None:
        self.runner = runner
        self.guard = guard
        self.context_fn = context_fn

    async def run_sql(self, args: RunSqlToolArgs, context: ToolContext) -> pd.DataFrame:
        safe_sql = self.guard.enforce(args.sql, self.context_fn(context))
        return await self.runner.run_sql(RunSqlToolArgs(sql=safe_sql), context)


async def demo(workdir: Path) -> None:
    path = create_demo_database(workdir / "sales.db")
    tools = ToolRegistry()
    runner = GuardedSqlRunner(SqliteRunner(database_path=str(path)), Guard(POLICY))
    run_sql = RunSqlTool(sql_runner=runner, file_system=LocalFileSystem(str(workdir)))
    tools.register_local_tool(run_sql, access_groups=["user"])

    # What a UserResolver would produce for the signed-in branch manager.
    user = User(id="ana", group_memberships=["user"], metadata={"branches": [1, 2]})
    context = ToolContext(
        user=user, conversation_id="demo", request_id="1", agent_memory=DemoAgentMemory()
    )
    for i, sql in enumerate((ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL)):
        # The tool calls a model would make.
        call = ToolCall(id=str(i), name="run_sql", arguments={"sql": sql})
        result = await tools.execute(call, context)
        print(f"{sql}\n  -> {result.result_for_llm.splitlines()[0:2]}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(demo(Path(tmp)))


if __name__ == "__main__":
    main()
