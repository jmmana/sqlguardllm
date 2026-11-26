"""The policy compiled once per Guard, ready for the rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from sqlguardllm._names import TableName


@dataclass(frozen=True)
class RowFilter:
    """A row filter parsed with sentinel columns in place of its placeholders."""

    table: TableName
    template: str
    condition: exp.Expression
    placeholders: Mapping[str, str]  # sentinel column name -> context path


@dataclass(frozen=True)
class Compiled:
    """Everything the rules need, normalized for the dialect."""

    dialect: Dialect
    dialect_name: str
    language: str
    tables: tuple[TableName, ...] | None
    hidden: Mapping[TableName, frozenset[str]]
    filters: Mapping[TableName, RowFilter]
    denied: frozenset[str]
    allowed: frozenset[str] | None
    max_rows: int | None
    default_schema: str | None
    output_dialect_name: str
    max_joins: int | None = None
    max_subquery_depth: int | None = None
    forbid_cartesian: bool = False
    required: Mapping[TableName, frozenset[str]] = field(default_factory=dict)
    masked: Mapping[TableName, Mapping[str, str]] = field(default_factory=dict)
    aggregate_only: Mapping[TableName, int] = field(default_factory=dict)
    schema: Mapping[TableName, tuple[str, ...]] = field(default_factory=dict)
