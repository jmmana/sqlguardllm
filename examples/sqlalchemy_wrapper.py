"""Recipe: guard SQL executed through SQLAlchemy 2.x.

Needs ``pip install "sqlalchemy>=2,<3"``. Run it::

    python examples/sqlalchemy_wrapper.py
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
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
from sqlalchemy import Engine, Row, create_engine

from sqlguardllm import Guard, GuardViolation


def run_sql(
    engine: Engine, guard: Guard, sql: str, context: Mapping[str, Any]
) -> Sequence[Row[Any]]:
    """Check and rewrite the model's SQL, then run the safe SQL. Raises ``GuardViolation``."""
    safe_sql = guard.enforce(sql, context)
    with engine.connect() as conn:
        # exec_driver_sql passes the SQL as is; text() would treat ":word" as a bind parameter.
        return conn.exec_driver_sql(safe_sql).all()


def main() -> None:
    guard = Guard(POLICY)
    with tempfile.TemporaryDirectory() as tmp:
        path = create_demo_database(Path(tmp) / "sales.db")
        # In production, connect as a database user that can only read (see THREAT_MODEL.md).
        engine = create_engine(f"sqlite:///{path}")
        try:
            for sql in (ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL):
                try:
                    print(f"{sql}\n  -> {list(run_sql(engine, guard, sql, CONTEXT))!r}")
                except GuardViolation as exc:
                    print(f"{sql}\n  -> rejected: {exc}")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
