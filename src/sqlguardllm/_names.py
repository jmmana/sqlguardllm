"""Table and column names, normalized with each dialect's identifier rules."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError

from sqlguardllm.result import PolicyError


@dataclass(frozen=True)
class TableName:
    """A normalized, possibly qualified table name."""

    name: str
    db: str | None = None
    catalog: str | None = None

    def __str__(self) -> str:
        return ".".join(part for part in (self.catalog, self.db, self.name) if part)


def norm(identifier: exp.Expression | str, dialect: Dialect) -> str:
    """Normalize an identifier: unquoted ones follow the dialect's case rules."""
    node = (
        identifier if isinstance(identifier, exp.Identifier) else exp.to_identifier(str(identifier))
    )
    normalized = dialect.normalize_identifier(node.copy())
    return normalized.name


def _part(table: exp.Table, key: str, dialect: Dialect) -> str | None:
    value = table.args.get(key)
    if value is None:
        return None
    if isinstance(value, exp.Identifier):
        return norm(value, dialect)
    return value.name or None


def table_name(table: exp.Table, dialect: Dialect) -> TableName:
    """The normalized name of a table reference (its ``this`` must be an identifier)."""
    return TableName(
        name=norm(table.this, dialect),
        db=_part(table, "db", dialect),
        catalog=_part(table, "catalog", dialect),
    )


def parse_table_entry(entry: str, dialect: Dialect, dialect_name: str) -> TableName:
    """Parse a policy table entry such as ``"invoices"`` or ``"sales.customers"``."""
    try:
        table = exp.to_table(entry, dialect=dialect_name)
    except SqlglotError as exc:
        raise PolicyError(f"invalid table name in policy: {entry!r}") from exc
    if not isinstance(table.this, exp.Identifier):
        raise PolicyError(f"invalid table name in policy: {entry!r}")
    return table_name(table, dialect)


def parse_column_entry(entry: str, dialect: Dialect, dialect_name: str) -> tuple[TableName, str]:
    """Parse a hidden column entry such as ``"customers.email"``."""
    try:
        column = exp.to_column(entry, dialect=dialect_name)
    except SqlglotError as exc:
        raise PolicyError(f"invalid hidden column in policy: {entry!r}") from exc
    if not isinstance(column, exp.Column) or not column.table:
        raise PolicyError(f"hidden columns must be written as table.column, got {entry!r}")
    table = TableName(
        name=norm(column.args["table"], dialect),
        db=norm(column.args["db"], dialect) if column.args.get("db") else None,
        catalog=norm(column.args["catalog"], dialect) if column.args.get("catalog") else None,
    )
    return table, norm(column.this, dialect)


def matches(entry: TableName, ref: TableName, default_schema: str | None) -> bool:
    """Whether a reference in a query is the table named by a policy entry.

    Unqualified entries only match unqualified references (or references to the default
    schema), so allowing ``invoices`` never allows ``other_schema.invoices``.
    """
    if entry.name != ref.name:
        return False
    if (entry.catalog is not None or ref.catalog is not None) and entry.catalog != ref.catalog:
        return False
    ref_db = ref.db if ref.db is not None else default_schema
    if entry.db is None:
        return ref.db is None or (default_schema is not None and ref.db == default_schema)
    return entry.db == ref_db


def find(
    entries: Iterable[TableName], ref: TableName, default_schema: str | None
) -> TableName | None:
    """The first policy entry that names ``ref``."""
    for entry in entries:
        if matches(entry, ref, default_schema):
            return entry
    return None
