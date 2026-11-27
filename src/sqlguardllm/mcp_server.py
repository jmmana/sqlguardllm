"""MCP server: a database tool for any MCP client that can't write, see hidden data or escape
row filters.

    sqlguardllm mcp --policy policy.yaml --db sqlite:///demo.db --context '{"user": {"id": 7}}'

Needs the optional extra ``pip install "sqlguardllm[mcp]"`` (MCP Python SDK, SQLAlchemy, PyYAML);
``import sqlguardllm`` never imports this module. Every query goes through
:meth:`~sqlguardllm.Guard.enforce` and runs in a transaction that is read-only where the database
supports it, bounded by a timeout, and always rolled back. The context and the database URL never
appear in tool results, error messages or logs.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection, Engine, Inspector
from sqlalchemy.engine.interfaces import ReflectedColumn, ReflectedForeignKeyConstraint
from sqlalchemy.exc import (
    ArgumentError,
    DBAPIError,
    NoSuchModuleError,
    NoSuchTableError,
    SQLAlchemyError,
)
from sqlglot.errors import SqlglotError

from sqlguardllm import __version__
from sqlguardllm._compiled import Compiled
from sqlguardllm._cost import CostEstimate, explain_statement, parse_plan_cost
from sqlguardllm._names import TableName, find, norm, parse_table_entry
from sqlguardllm.audit import AuditEvent, AuditLog
from sqlguardllm.guard import Guard
from sqlguardllm.messages import violation
from sqlguardllm.policy import Policy
from sqlguardllm.result import GuardViolation, PolicyError, Violation, ViolationCode

__all__ = ["DEFAULT_MAX_ROWS", "DEFAULT_TIMEOUT", "create_server"]

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
"""Seconds a query may run before it is cancelled."""

DEFAULT_MAX_ROWS = 1000
"""Rows returned per query when the policy has no ``max_rows``."""

_TEXT: dict[str, dict[str, str]] = {
    "not_found": {
        "en": "{table} was not found in the database",
        "es": "{table} no existe en la base de datos",
    },
    "bad_name": {
        "en": "{name!r} is not a valid table name",
        "es": "{name!r} no es un nombre de tabla válido",
    },
    "timeout": {
        "en": "The query took longer than {seconds:g} s and was cancelled",
        "es": "La consulta tardó más de {seconds:g} s y se canceló",
    },
    "rejected_by_db": {
        "en": "The database rejected the query: {detail}",
        "es": "La base de datos rechazó la consulta: {detail}",
    },
    "too_costly": {
        "en": (
            "The query is too expensive: the database estimates {estimate}, above the limit of "
            "{limit}. Add a filter (WHERE) or select fewer columns and try again"
        ),
        "es": (
            "La consulta es demasiado costosa: la base de datos estima {estimate}, por encima "
            "del límite de {limit}. Añade un filtro (WHERE) o selecciona menos columnas e "
            "inténtalo de nuevo"
        ),
    },
    "cost_unknown": {
        "en": "The query cost could not be estimated, so the query was not run: {detail}",
        "es": "No se pudo estimar el costo de la consulta, así que no se ejecutó: {detail}",
    },
    "unavailable": {
        "en": "The database is not available right now; try again later",
        "es": "La base de datos no está disponible en este momento; inténtalo más tarde",
    },
}

# SQLAlchemy backend name -> sqlglot dialect, to warn when the policy targets another database.
_SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "mssql": "tsql",
    "sqlite": "sqlite",
    "mysql": "mysql",
    "mariadb": "mysql",
    "oracle": "oracle",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "trino": "trino",
    "databricks": "databricks",
    "redshift": "redshift",
}

# Module-level so the SDK can evaluate the annotations of the tool functions.
_TableArg = Annotated[str, Field(description="Table name, as list_tables shows it")]
_SqlArg = Annotated[str, Field(description="One SELECT query, in the dialect list_tables reports")]

_READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

_INSTRUCTIONS = """\
Read-only SQL access to a database, guarded by sqlguardllm.
Write {dialect} SQL: one SELECT per call (CTEs and subqueries are fine).
Start with list_tables, then describe_table for the tables you need.
Every query is checked against a policy: only the listed tables and columns, no writes,
no dangerous functions. Rows are filtered for the current user automatically and limited
to {max_rows} per query. When a query is rejected, the error says why: fix the query and
try again instead of working around the policy."""


def _first_line(text: str, secret: str | None = None) -> str:
    lines = text.strip().splitlines()
    line = lines[0].strip() if lines else ""
    if secret:
        line = line.replace(secret, "***")
    return line[:300]


def _jsonable(value: Any) -> Any:
    """A database value as JSON: exact decimals stay exact, binary becomes base64."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        if value == value.to_integral_value():
            return int(value)
        as_float = float(value)
        return as_float if Decimal(repr(as_float)) == value else str(value)
    if isinstance(value, datetime | date | clock_time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _type_name(column_type: Any) -> str:
    try:
        return str(column_type)
    except Exception:  # some reflected types can't be rendered without a dialect
        return type(column_type).__name__


def _violation_text(violations: tuple[Violation, ...] | list[Violation]) -> str:
    return "\n".join(f"{item.code.value}: {item.message}" for item in violations)


def _begin_read_only(conn: Connection, timeout: float) -> Callable[[], None]:
    """Make the open transaction read-only and bounded in time, as far as the backend allows.

    Returns a function that undoes per-query settings before the connection goes back to the
    pool. Backends not listed here rely on the read-only database user and the final rollback.
    """
    backend = conn.dialect.name
    dbapi: Any = conn.connection.dbapi_connection
    millis = max(1, math.ceil(timeout * 1000))
    if backend == "sqlite":
        conn.exec_driver_sql("PRAGMA query_only = ON")
        deadline = time.monotonic() + timeout
        dbapi.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return lambda: dbapi.set_progress_handler(None, 0)
    if backend == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {millis}")
    elif backend in ("mysql", "mariadb"):
        if getattr(conn.dialect, "is_mariadb", False):
            conn.exec_driver_sql(f"SET SESSION max_statement_time = {millis / 1000:.3f}")
        else:
            conn.exec_driver_sql(f"SET SESSION max_execution_time = {millis}")
        conn.exec_driver_sql("START TRANSACTION READ ONLY")
    elif backend == "oracle":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        if hasattr(dbapi, "call_timeout"):
            dbapi.call_timeout = millis
    elif backend == "mssql" and hasattr(dbapi, "timeout"):
        dbapi.timeout = math.ceil(timeout)  # pyodbc's query timeout, in seconds
    return lambda: None


Estimator = Callable[[Connection, str], CostEstimate]
"""Gets the planner's estimate for a query on an open connection (``EXPLAIN``, never run it)."""

_COST_BACKENDS = ("postgresql", "mysql")


def _explain_estimate(conn: Connection, sql: str) -> CostEstimate:
    """Ask the engine for its plan in JSON and read the estimate. Never executes ``sql``."""
    statement = explain_statement(conn.dialect.name, sql)
    if statement is None:
        raise ValueError(f"cost estimates are not supported for {conn.dialect.name}")
    text = conn.exec_driver_sql(statement).scalar()
    if isinstance(text, bytes | bytearray):
        text = bytes(text).decode()
    if not isinstance(text, str):
        text = json.dumps(text)  # psycopg may already decode json
    return parse_plan_cost(conn.dialect.name, text)


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:g}"


class _CostRefused(Exception):
    """The cost guard refused the query; the message is for the model."""


class _Tools:
    """The four tools, bound to one guard, one database and one caller's context."""

    def __init__(
        self,
        guard: Guard,
        engine: Engine,
        context: Mapping[str, Any] | None,
        language: str,
        timeout: float,
        *,
        max_cost: float | None = None,
        max_plan_rows: float | None = None,
        estimator: Estimator | None = None,
    ) -> None:
        self.max_cost = max_cost
        self.max_plan_rows = max_plan_rows
        self.estimator = estimator
        self.guard = guard
        self.engine = engine
        self._context = context
        self.language = language
        self.timeout = timeout
        policy = guard.policy
        self.dialect = policy.dialect
        self.max_rows = policy.max_rows if policy.max_rows is not None else DEFAULT_MAX_ROWS
        # The policy compiled by the guard: names already normalized for the dialect.
        self.compiled: Compiled = guard._compiled
        self._password = engine.url.password if isinstance(engine.url.password, str) else None

    # Helpers ---------------------------------------------------------------------------------

    def _text(self, key: str, **values: Any) -> str:
        templates = _TEXT[key]
        return templates.get(self.language, templates["en"]).format(**values)

    def _hidden(self, table: TableName) -> frozenset[str]:
        """Hidden columns of a table, casefolded. Matching by name alone hides more, never less."""
        return frozenset(
            column.casefold()
            for entry, columns in self.compiled.hidden.items()
            if entry.name == table.name
            for column in columns
        )

    def _masked(self, table: TableName) -> dict[str, str]:
        """Masked columns of a table, casefolded, with their strategy."""
        entry = self._marker(self.compiled.masked.keys(), table)
        if entry is None:
            return {}
        return {
            column.casefold(): strategy for column, strategy in self.compiled.masked[entry].items()
        }

    def _marker(self, entries: Iterable[TableName], table: TableName) -> TableName | None:
        return find(entries, table, self.compiled.default_schema)

    def _allowed_tables(self, inspector: Inspector) -> list[TableName]:
        if self.compiled.tables is not None:
            return list(self.compiled.tables)
        schema = self.guard.policy.default_schema
        names = inspector.get_table_names(schema=schema)
        names += inspector.get_view_names(schema=schema)
        dialect = self.compiled.dialect
        return sorted({TableName(name=norm(name, dialect), db=schema) for name in names}, key=str)

    def _resolve(self, name: str) -> TableName:
        """The policy entry for a table name written by the model, or a tool error."""
        try:
            ref = parse_table_entry(name, self.compiled.dialect, self.compiled.dialect_name)
        except (PolicyError, SqlglotError, ValueError):
            raise ToolError(self._text("bad_name", name=name)) from None
        if self.compiled.tables is None:
            return ref
        entry = find(self.compiled.tables, ref, self.compiled.default_schema)
        if entry is None:
            item = violation(ViolationCode.TABLE_NOT_ALLOWED, name, self.language)
            raise ToolError(_violation_text([item]))
        return entry

    @staticmethod
    def _location(table: TableName) -> tuple[str, str | None]:
        schema = ".".join(part for part in (table.catalog, table.db) if part) or None
        return table.name, schema

    def _columns(self, inspector: Inspector, table: TableName) -> list[ReflectedColumn]:
        name, schema = self._location(table)
        hidden = self._hidden(table)
        return [
            column
            for column in inspector.get_columns(name, schema=schema)
            if str(column["name"]).casefold() not in hidden
        ]

    def _inspector(self) -> Inspector:
        try:
            return inspect(self.engine)
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from None

    def _unavailable(self, exc: Exception) -> ToolError:
        detail = exc.orig if isinstance(exc, DBAPIError) else exc
        logger.warning(
            "database error: %s: %s",
            type(detail).__name__,
            _first_line(str(detail), self._password),
        )
        return ToolError(self._text("unavailable"))

    # Tools -----------------------------------------------------------------------------------

    def list_tables(self) -> dict[str, Any]:
        """List the tables you may query, with the columns you can use."""
        inspector = self._inspector()
        tables: list[dict[str, Any]] = []
        try:
            for table in self._allowed_tables(inspector):
                try:
                    columns = self._columns(inspector, table)
                except NoSuchTableError:
                    logger.warning("table %s is in the policy but not in the database", table)
                    continue
                names = [str(column["name"]) for column in columns]
                tables.append({"name": str(table), "columns": names, **self._markers(table, names)})
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from None
        return {"dialect": self.dialect, "max_rows": self.max_rows, "tables": tables}

    def _markers(self, table: TableName, columns: list[str]) -> dict[str, Any]:
        """What list_tables says about a table beyond its columns, only when it applies."""
        markers: dict[str, Any] = {}
        masked = self._masked(table)
        if masked:
            markers["masked_columns"] = {
                column: masked[column.casefold()]
                for column in columns
                if column.casefold() in masked
            }
        aggregate = self._marker(self.compiled.aggregate_only.keys(), table)
        if aggregate is not None:
            markers["aggregate_only"] = self.compiled.aggregate_only[aggregate]
        required = self._marker(self.compiled.required.keys(), table)
        if required is not None:
            markers["required_filters"] = sorted(self.compiled.required[required])
        return markers

    def describe_table(self, table: _TableArg) -> dict[str, Any]:
        """Describe one table: columns, keys and how the policy treats it."""
        entry = self._resolve(table)
        name, schema = self._location(entry)
        inspector = self._inspector()
        try:
            try:
                columns = self._columns(inspector, entry)
            except NoSuchTableError:
                raise ToolError(self._text("not_found", table=str(entry))) from None
            primary = set(inspector.get_pk_constraint(name, schema=schema)["constrained_columns"])
            foreign = inspector.get_foreign_keys(name, schema=schema)
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from None

        masked = self._masked(entry)
        hidden = self._hidden(entry)
        visible = {str(column["name"]).casefold() for column in columns}
        described: list[dict[str, Any]] = []
        for column in columns:
            item: dict[str, Any] = {
                "name": str(column["name"]),
                "type": _type_name(column["type"]),
                "nullable": bool(column.get("nullable", True)),
                "primary_key": column["name"] in primary,
            }
            strategy = masked.get(str(column["name"]).casefold())
            if strategy is not None:
                item["masked"] = strategy
            described.append(item)

        aggregate = self._marker(self.compiled.aggregate_only.keys(), entry)
        required = self._marker(self.compiled.required.keys(), entry)
        star = not hidden and aggregate is None
        if masked and self._marker(self.compiled.schema.keys(), entry) is None:
            star = False
        return {
            "table": str(entry),
            "columns": described,
            "foreign_keys": self._foreign_keys(foreign, visible),
            "rows_filtered_for_user": self._marker(self.compiled.filters.keys(), entry) is not None,
            "required_filters": (
                sorted(self.compiled.required[required]) if required is not None else []
            ),
            "aggregate_only": (
                self.compiled.aggregate_only[aggregate] if aggregate is not None else None
            ),
            "select_star_allowed": star,
        }

    def _foreign_keys(
        self, keys: list[ReflectedForeignKeyConstraint], visible: set[str]
    ) -> list[dict[str, Any]]:
        """Foreign keys between visible columns of allowed tables; the rest isn't shown."""
        dialect = self.compiled.dialect
        found: list[dict[str, Any]] = []
        for key in keys:
            columns = [str(column) for column in key.get("constrained_columns") or []]
            referred_columns = [str(column) for column in key.get("referred_columns") or []]
            referred_table = key.get("referred_table")
            if not columns or not referred_table:
                continue
            schema = key.get("referred_schema")
            ref = TableName(
                name=norm(str(referred_table), dialect),
                db=norm(str(schema), dialect) if schema else None,
            )
            if self.compiled.tables is not None:
                entry = find(self.compiled.tables, ref, self.compiled.default_schema)
                if entry is None:
                    continue
                ref = entry
            if any(column.casefold() not in visible for column in columns):
                continue
            if any(column.casefold() in self._hidden(ref) for column in referred_columns):
                continue
            found.append(
                {
                    "columns": columns,
                    "references": str(ref),
                    "referred_columns": referred_columns,
                }
            )
        return found

    def explain_query(self, sql: _SqlArg) -> dict[str, Any]:
        """Check a query against the policy without running it."""
        explanation = self.guard.explain(sql, self._context)
        # Not explanation.text (meant for people and audit logs), and not explanation.sql: the
        # rewritten SQL has the user's context values bound into the row filters. The model gets
        # each filter as written in the policy, with its {user.…} placeholders.
        templates = self.guard.policy.row_filters
        report = self._estimate_report(sql) if explanation.ok and self.cost_guard else None
        extra = {"estimate": report} if report is not None else {}
        return {
            **extra,
            "ok": explanation.ok,
            "tables": sorted(explanation.tables),
            "columns": {
                table: sorted(columns) for table, columns in sorted(explanation.columns.items())
            },
            "unresolved_columns": sorted(explanation.unresolved_columns),
            "masked_columns": dict(explanation.masked_columns),
            "aggregate_only": dict(explanation.aggregate_only),
            "row_filters": {
                table: templates.get(table, "(filtered for this user)")
                for table in sorted(explanation.row_filters)
            },
            "limit": explanation.limit,
            "violations": [
                {"code": item.code.value, "message": item.message, "detail": item.detail}
                for item in explanation.violations
            ],
        }

    @property
    def cost_guard(self) -> bool:
        return self.max_cost is not None or self.max_plan_rows is not None

    def _check_cost(self, conn: Connection, sql: str) -> None:
        """Refuse a query whose estimate is over a threshold. Fails closed when one is set."""
        if not self.cost_guard:
            return
        try:
            estimate = (self.estimator or _explain_estimate)(conn, sql)
        except Exception as exc:  # EXPLAIN failed or its output can't be read: don't run blind
            if isinstance(exc, DBAPIError) and exc.connection_invalidated:
                raise
            detail = exc.orig if isinstance(exc, DBAPIError) else exc
            logger.warning(
                "cost check failed: %s: %s",
                type(detail).__name__,
                _first_line(str(detail), self._password),
            )
            raise _CostRefused(
                self._text("cost_unknown", detail=_first_line(str(detail), self._password))
            ) from None
        over: list[tuple[str, float, float]] = []
        if self.max_cost is not None and estimate.cost > self.max_cost:
            over.append(("cost", estimate.cost, self.max_cost))
        if self.max_plan_rows is not None:
            if estimate.rows is None:
                raise _CostRefused(
                    self._text("cost_unknown", detail="the plan has no row estimate")
                )
            if estimate.rows > self.max_plan_rows:
                over.append(("rows", estimate.rows, self.max_plan_rows))
        if over:
            estimated = ", ".join(f"{name} {_fmt(value)}" for name, value, _ in over)
            limits = ", ".join(f"{name} {_fmt(limit)}" for name, _, limit in over)
            raise _CostRefused(self._text("too_costly", estimate=estimated, limit=limits))

    def _estimate_report(self, sql: str) -> dict[str, Any] | None:
        """The planner's estimate for explain_query, or None when unavailable."""
        if self.estimator is None and self.engine.dialect.name not in _COST_BACKENDS:
            return None
        try:
            safe_sql = self.guard.enforce(sql, self._context)
            with self.engine.connect() as raw:
                conn = raw.execution_options(no_parameters=True)
                transaction = conn.begin()
                try:
                    restore = _begin_read_only(conn, self.timeout)
                    try:
                        estimate = (self.estimator or _explain_estimate)(conn, safe_sql)
                    finally:
                        restore()
                finally:
                    transaction.rollback()
        except Exception:
            return None
        report: dict[str, Any] = {"cost": estimate.cost, "rows": estimate.rows}
        if self.max_cost is not None:
            report["max_cost"] = self.max_cost
        if self.max_plan_rows is not None:
            report["max_plan_rows"] = self.max_plan_rows
        return report

    def query(self, sql: _SqlArg) -> dict[str, Any]:
        """Run a read-only query and return its rows."""
        try:
            safe_sql = self.guard.enforce(sql, self._context)
        except GuardViolation as exc:
            raise ToolError(_violation_text(exc.violations)) from None

        try:
            conn = self.engine.connect()
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from None
        started = time.monotonic()
        try:
            # no_parameters: percent signs in LIKE patterns reach the driver untouched.
            conn = conn.execution_options(no_parameters=True)
            transaction = conn.begin()
            try:
                restore = _begin_read_only(conn, self.timeout)
                try:
                    self._check_cost(conn, safe_sql)
                    result = conn.exec_driver_sql(safe_sql)
                    columns = list(map(str, result.keys()))  # not iter(result): rows
                    rows = result.fetchmany(self.max_rows)
                    result.close()
                finally:
                    restore()
            finally:
                transaction.rollback()
        except _CostRefused as refusal:
            raise ToolError(str(refusal)) from None
        except DBAPIError as exc:
            if time.monotonic() - started >= self.timeout:
                raise ToolError(self._text("timeout", seconds=self.timeout)) from None
            if exc.connection_invalidated:
                raise self._unavailable(exc) from None
            detail = _first_line(str(exc.orig), self._password) or type(exc.orig).__name__
            raise ToolError(self._text("rejected_by_db", detail=detail)) from None
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from None
        finally:
            conn.close()

        return {
            "columns": columns,
            "rows": [[_jsonable(value) for value in row] for row in rows],
            "row_count": len(rows),
            "limit_reached": len(rows) >= self.max_rows,
        }


def _create_engine(url: str) -> Engine:
    """An engine for ``url``. Error messages never include the URL, which may hold a password."""
    try:
        return create_engine(url, pool_pre_ping=True)
    except NoSuchModuleError:
        raise ValueError(
            "the database URL names a database or driver SQLAlchemy doesn't know"
        ) from None
    except ArgumentError:
        raise ValueError("the database URL is not valid") from None
    except ImportError as exc:
        raise ValueError(
            f"the database driver is not installed: {exc.name or 'unknown module'}"
        ) from None


def create_server(
    policy: Policy,
    database: str | Engine,
    *,
    context: Mapping[str, Any] | None = None,
    language: str = "en",
    timeout: float = DEFAULT_TIMEOUT,
    name: str = "sqlguardllm",
    max_cost: float | None = None,
    max_plan_rows: float | None = None,
    estimator: Estimator | None = None,
    audit: AuditLog | Callable[[AuditEvent], None] | None = None,
    role: str | None = None,
) -> MCPServer[Any]:
    """Build the MCP server: four read-only tools over ``database``, guarded by ``policy``.

    Args:
        policy: What the model may read.
        database: A SQLAlchemy URL or engine. Connect with a read-only database user.
        context: Values for row-filter placeholders, from the operator, never from the model.
        language: Language of violation messages, ``"en"`` or ``"es"``.
        timeout: Seconds a query may run before it is cancelled.
        name: Server name shown by MCP clients.
        max_cost: Refuse queries whose planner cost (``EXPLAIN``, PostgreSQL and MySQL) is above
            this. Ignored, with a warning, on other engines. Fails closed if the estimate fails.
        max_plan_rows: Same, for the planner's row estimate.
        estimator: Replaces the ``EXPLAIN`` estimate; meant for tests.
        audit: Record every decision of the ``query`` tool (see docs/AUDIT.md). ``None``
            (default) records nothing. ``explain_query`` is not a decision and is not recorded.
        role: Role name for the audit events.

    Raises:
        PolicyError: If the policy is invalid.
        ValueError: If a threshold or the timeout is invalid, the database URL is invalid or the
            driver is missing.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ValueError("timeout must be a number of seconds")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    for label, limit in (("max_cost", max_cost), ("max_plan_rows", max_plan_rows)):
        if limit is None:
            continue
        if isinstance(limit, bool) or not isinstance(limit, int | float):
            raise ValueError(f"{label} must be a number")
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError(f"{label} must be a positive number")
    guard = Guard(policy, language=language, audit=audit, role=role)
    owned = isinstance(database, str)
    engine = _create_engine(database) if isinstance(database, str) else database

    target = (policy.output_dialect or policy.dialect).lower()
    backend = _SQLGLOT_DIALECTS.get(engine.dialect.name)
    if backend is not None and backend != target:
        logger.warning(
            "the policy generates %s SQL but the database is %s", target, engine.dialect.name
        )

    unsupported = engine.dialect.name not in _COST_BACKENDS
    if (max_cost is not None or max_plan_rows is not None) and estimator is None and unsupported:
        logger.warning(
            "the cost guard (max_cost, max_plan_rows) is ignored: %s has no supported "
            "EXPLAIN estimate (only PostgreSQL and MySQL); queries run without a cost check",
            engine.dialect.name,
        )
        max_cost = max_plan_rows = None
    tools = _Tools(
        guard,
        engine,
        context,
        language,
        float(timeout),
        max_cost=None if max_cost is None else float(max_cost),
        max_plan_rows=None if max_plan_rows is None else float(max_plan_rows),
        estimator=estimator,
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned:
                engine.dispose()

    server: MCPServer[Any] = MCPServer(
        name,
        title="sqlguardllm",
        version=__version__,
        instructions=_INSTRUCTIONS.format(dialect=policy.dialect, max_rows=tools.max_rows),
        lifespan=lifespan,
    )

    server.add_tool(
        tools.list_tables,
        name="list_tables",
        title="List tables",
        description=(
            "List the tables you may query, with the columns you can use, the SQL dialect and "
            "the row limit. Call this first. Columns that aren't listed are hidden and rejected "
            "anywhere in a query. Tables may also show masked columns (selectable, but they "
            "come back masked), aggregate_only (only aggregates and GROUP BY, groups of at "
            "least that many rows) and required_filters (filter on one of those columns)."
        ),
        annotations=_READ_ONLY,
    )
    server.add_tool(
        tools.describe_table,
        name="describe_table",
        title="Describe a table",
        description=(
            "Describe one table you may query: column names and types, primary key, foreign "
            "keys to other tables you may query, masked columns, whether rows are filtered for "
            "the current user, required filters, aggregate-only and whether SELECT * works."
        ),
        annotations=_READ_ONLY,
    )
    server.add_tool(
        tools.explain_query,
        name="explain_query",
        title="Explain a query",
        description=(
            "Check a query against the policy without running it. Returns ok, the SQL that "
            "would actually run (with the current user's row filters, masks and row limit "
            "added), the tables and columns it reads, or the violations that explain why it "
            "would be rejected."
        ),
        annotations=_READ_ONLY,
    )
    server.add_tool(
        tools.query,
        name="query",
        title="Run a read-only query",
        description=(
            f"Run one read-only {policy.dialect} SELECT and return columns and rows as JSON, "
            f"at most {tools.max_rows} rows. Writes, other statements, tables or columns outside "
            "the policy and dangerous functions are rejected with the reason: fix the query and "
            "try again. Rows are filtered for the current user automatically; don't add those "
            "filters yourself."
        ),
        annotations=_READ_ONLY,
    )
    return server
