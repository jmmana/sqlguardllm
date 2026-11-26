"""Build ``tables`` and ``schema`` of a policy by reading a live database.

Needs the optional extra: ``pip install "sqlguardllm[db]"`` plus the driver of your database.
Only table and column names are read; no row is ever fetched.
"""

from __future__ import annotations

from collections.abc import Collection
from fnmatch import fnmatchcase
from typing import Any

from sqlguardllm.result import PolicyError

SYSTEM_SCHEMAS = frozenset(
    {"information_schema", "pg_catalog", "pg_toast", "sys", "mysql", "performance_schema"}
)


def read_database(
    url: str,
    *,
    include: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
    schemas: Collection[str] | None = None,
) -> tuple[dict[str, tuple[str, ...]], str]:
    """Return ``({table: columns}, dialect)`` for the tables that match the filters.

    ``include`` and ``exclude`` are glob patterns over the table name (``"invoice*"``); a table
    in a non-default schema is written ``schema.table``. Views are included.
    """
    try:
        from sqlalchemy import create_engine, inspect  # noqa: PLC0415
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyError('Policy.from_database needs: pip install "sqlguardllm[db]"') from exc

    try:
        engine = create_engine(url)
    except (SQLAlchemyError, ImportError, ValueError) as exc:
        raise PolicyError(f"cannot open the database: {type(exc).__name__}") from exc
    try:
        inspector: Any = inspect(engine)
        default = inspector.default_schema_name
        wanted = (
            list(schemas)
            if schemas
            else [s for s in inspector.get_schema_names() if s.casefold() not in SYSTEM_SCHEMAS]
        )
        found: dict[str, tuple[str, ...]] = {}
        for schema in wanted:
            sql_schema = None if schema == default else schema
            names = [
                *inspector.get_table_names(schema=sql_schema),
                *inspector.get_view_names(schema=sql_schema),
            ]
            for name in sorted(set(names)):
                qualified = name if sql_schema is None else f"{schema}.{name}"
                if include and not any(fnmatchcase(qualified, p) for p in include):
                    continue
                if exclude and any(fnmatchcase(qualified, p) for p in exclude):
                    continue
                columns = inspector.get_columns(name, schema=sql_schema)
                found[qualified] = tuple(str(c["name"]) for c in columns)
        return found, engine.dialect.name
    except SQLAlchemyError as exc:
        # The message can carry the URL (and its password): report only the type.
        raise PolicyError(f"cannot read the database: {type(exc).__name__}") from exc
    finally:
        engine.dispose()
