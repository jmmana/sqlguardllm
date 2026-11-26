"""Recipe: langchain-community's SQL toolkit, guarded by sqlguardllm.

For existing code built on ``SQLDatabase``, ``SQLDatabaseToolkit`` or ``QuerySQLDatabaseTool``
from langchain-community 0.4. That package was archived in 2026 (no more releases); for new
code see ``langchain_sql.py``. Needs ``pip install "langchain-community>=0.4,<0.5"``. Run it::

    python examples/langchain_community_sql.py

With a real model::

    db = GuardedSQLDatabase.from_uri(url, guard=guard, context_fn=current_user_context,
                                     include_tables=sorted(policy.tables),
                                     sample_rows_in_table_info=0)
    agent = create_agent(model, SQLDatabaseToolkit(db=db, llm=model).get_tools())
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from demo_data import (
    ALLOWED_SQL,
    CONTEXT,
    HIDDEN_COLUMN_SQL,
    POLICY,
    WRITE_SQL,
    create_demo_database,
)
from langchain_community.tools import QuerySQLDatabaseTool
from langchain_community.utilities import SQLDatabase

from sqlguardllm import Guard, GuardViolation


class GuardedSQLDatabase(SQLDatabase):
    """A LangChain ``SQLDatabase`` whose ``run`` only executes SQL rewritten by sqlguardllm.

    Every tool of ``SQLDatabaseToolkit`` that runs SQL goes through ``run``. A rejected query
    comes back to the model as ``"Error: <reasons>"``, like a database error, so it can fix it.

    Create it with ``sample_rows_in_table_info=0``: sample rows are read straight from the
    tables, outside ``run``, and would show hidden columns and rows of other users to the model.
    """

    def __init__(
        self,
        *args: Any,
        guard: Guard,
        context_fn: Callable[[], Mapping[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.guard = guard
        self.context_fn = context_fn

    def run(self, command: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(command, str):  # a SQLAlchemy statement can't be checked
            raise TypeError("GuardedSQLDatabase only runs SQL strings")
        return super().run(self.guard.enforce(command, self.context_fn()), *args, **kwargs)

    def run_no_throw(self, command: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().run_no_throw(command, *args, **kwargs)  # calls self.run
        except GuardViolation as exc:
            return f"Error: {exc}"


def main() -> None:
    guard = Guard(POLICY)
    with tempfile.TemporaryDirectory() as tmp:
        path = create_demo_database(Path(tmp) / "sales.db")
        db = GuardedSQLDatabase.from_uri(
            f"sqlite:///{path}",
            guard=guard,
            context_fn=lambda: CONTEXT,
            include_tables=sorted(POLICY.tables or ()),
            sample_rows_in_table_info=0,
        )
        tool = QuerySQLDatabaseTool(db=db)  # the "sql_db_query" tool an agent calls
        for sql in (ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL):
            print(f"{sql}\n  -> {tool.invoke({'query': sql})}")


if __name__ == "__main__":
    main()
