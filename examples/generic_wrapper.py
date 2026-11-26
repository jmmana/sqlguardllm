"""Recipe: guard the function that executes the model's SQL, with any DB-API driver.

Works the same with sqlite3, psycopg, mysqlclient, pyodbc or oracledb: only the connection
changes. Needs nothing but sqlguardllm. Run it::

    python examples/generic_wrapper.py
"""

from __future__ import annotations

import sqlite3
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
    connect_read_only,
    create_demo_database,
)

from sqlguardllm import Guard, GuardViolation

Rows = list[tuple[Any, ...]]


def guarded_runner(
    conn: sqlite3.Connection,
    guard: Guard,
    context_fn: Callable[[], Mapping[str, Any]],
) -> Callable[[str], Rows]:
    """The one function your app or agent calls with the model's SQL.

    ``guard.wrap`` checks and rewrites the SQL before ``execute`` sees it, with the context of
    the current user from ``context_fn``, and raises ``GuardViolation`` instead of running it
    when it isn't allowed.
    """

    def execute(sql: str) -> Rows:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            return cursor.fetchall()
        finally:
            cursor.close()

    return guard.wrap(execute, context_fn=context_fn)


def answer_for_model(run_sql: Callable[[str], Rows], sql: str) -> str:
    """What goes back to the model: the rows, or why the query was rejected so it can fix it.

    Violation messages say what was wrong without revealing hidden data.
    """
    try:
        return f"Rows: {run_sql(sql)!r}"
    except GuardViolation as exc:
        return "Query rejected: " + "; ".join(v.message for v in exc.violations)


def main() -> None:
    guard = Guard(POLICY)  # validates the policy once; reuse it for every query
    with tempfile.TemporaryDirectory() as tmp:
        path = create_demo_database(Path(tmp) / "sales.db")
        conn = connect_read_only(path)
        try:
            run_sql = guarded_runner(conn, guard, context_fn=lambda: CONTEXT)
            for sql in (ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL):
                print(f"{sql}\n  -> {answer_for_model(run_sql, sql)}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
