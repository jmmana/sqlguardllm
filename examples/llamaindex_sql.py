"""Recipe: LlamaIndex text-to-SQL that only runs SQL sqlguardllm has made safe.

Targets llama-index-core 0.14 (``SQLDatabase``, ``NLSQLTableQueryEngine``, ``NLSQLRetriever``),
which all execute through ``SQLDatabase.run_sql``. Needs
``pip install "llama-index-core>=0.14,<0.15"``. Run it::

    python examples/llamaindex_sql.py

With a real model::

    from llama_index.core.query_engine import NLSQLTableQueryEngine

    engine = NLSQLTableQueryEngine(sql_database=db, tables=sorted(policy.tables), llm=llm)
    engine.query("Revenue of my branches?")   # a rejected query answers "Error: <reasons>"
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
from llama_index.core import SQLDatabase
from sqlalchemy import create_engine

from sqlguardllm import Guard, GuardViolation


class GuardedSQLDatabase(SQLDatabase):
    """A LlamaIndex ``SQLDatabase`` whose ``run_sql`` only executes SQL rewritten by sqlguardllm.

    Raises ``GuardViolation`` for a rejected query; the query engines turn it into an
    ``"Error: <reasons>"`` answer (``handle_sql_errors=True``, their default).

    Create it with ``sample_rows_in_table_info=0`` and without ``schema=``: sample rows are read
    outside ``run_sql``, and ``schema`` makes LlamaIndex edit the SQL after it was checked. Use
    the policy's ``default_schema`` instead.
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

    def run_sql(self, command: str) -> tuple[str, dict[str, Any]]:
        return super().run_sql(self.guard.enforce(command, self.context_fn()))


def main() -> None:
    guard = Guard(POLICY)
    with tempfile.TemporaryDirectory() as tmp:
        path = create_demo_database(Path(tmp) / "sales.db")
        engine = create_engine(f"sqlite:///{path}")  # a read-only user in production
        db = GuardedSQLDatabase(
            engine,
            guard=guard,
            context_fn=lambda: CONTEXT,
            include_tables=sorted(POLICY.tables or ()),
            sample_rows_in_table_info=0,
        )
        for sql in (ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL):
            try:
                print(f"{sql}\n  -> {db.run_sql(sql)[0]}")
            except GuardViolation as exc:
                print(f"{sql}\n  -> Error: {exc}")
        engine.dispose()


if __name__ == "__main__":
    main()
