"""``Guard.explain()``: what a query reads and what the guard does to it, in data and in words.

The explanation comes from the same pass as ``Guard.check()``; this module only adds the
columns read per table and the sentence for people.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import norm
from sqlguardllm.messages import LANGUAGES, violation
from sqlguardllm.result import Violation, ViolationCode
from sqlguardllm.rules.columns import _visible_sources, scope_columns
from sqlguardllm.rules.sources import PhysicalRef

STAR = "*"

_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "and": "and",
        "reads": "Reads {reads}",
        "reads_nothing": "Reads no table",
        "filtered": "filtered by {filters}",
        "filter": "{condition} on {table}",
        "limit": "up to {limit} rows",
        "limit_one": "up to 1 row",
        "no_limit": "with no row limit",
        "unresolved": "{column} (table not resolved)",
        "rejected": "Rejected: {reasons}.",
        "would_read": "It would read {reads}.",
    },
    "es": {
        "and": "y",
        "reads": "Lee {reads}",
        "reads_nothing": "No lee ninguna tabla",
        "filtered": "filtrado por {filters}",
        "filter": "{condition} en {table}",
        "limit": "hasta {limit} filas",
        "limit_one": "hasta 1 fila",
        "no_limit": "sin límite de filas",
        "unresolved": "{column} (tabla sin resolver)",
        "rejected": "Rechazada: {reasons}.",
        "would_read": "Leería {reads}.",
    },
}


@dataclass(frozen=True)
class Explanation:
    """What a query reads and what the guard does to it, from :meth:`sqlguardllm.Guard.explain`.

    The text and ``row_filters`` contain the context values bound into the row filters (the
    user's branches, tenant…): show them to the user or log them, but think twice before
    handing them to the model.

    Attributes:
        ok: True when the query may run, exactly as :meth:`~sqlguardllm.Guard.check` decides.
        sql: The rewritten SQL to execute (only when ``ok``).
        tables: Allowed physical tables the query reads, as normalized dotted names, also when
            it is rejected. Tables outside the allowlist appear in ``violations`` instead.
        columns: Table → columns the query references in it, in any clause and scope; ``"*"``
            for a star. A table read without naming columns (``COUNT(*)``) maps to an empty set.
        unresolved_columns: Unqualified columns of a ``SELECT`` with several sources, whose
            table can't be known without table metadata.
        row_filters: Table → row filter injected, with context values bound (only when ``ok``).
        limit: Row limit of the rewritten query; ``None`` when rejected or unlimited.
        violations: Every reason the query is rejected (empty when ``ok``).
        language: Language of ``text`` and of the violation messages.
    """

    ok: bool
    sql: str | None = None
    tables: frozenset[str] = frozenset()
    columns: Mapping[str, frozenset[str]] = field(default_factory=dict)
    unresolved_columns: frozenset[str] = frozenset()
    row_filters: Mapping[str, str] = field(default_factory=dict)
    limit: int | None = None
    violations: tuple[Violation, ...] = ()
    language: str = "en"

    def __post_init__(self) -> None:
        columns = {table: frozenset(names) for table, names in self.columns.items()}
        object.__setattr__(self, "columns", MappingProxyType(columns))
        object.__setattr__(self, "row_filters", MappingProxyType(dict(self.row_filters)))

    @property
    def codes(self) -> frozenset[ViolationCode]:
        """The set of violation codes."""
        return frozenset(v.code for v in self.violations)

    @property
    def text(self) -> str:
        """A short sentence for people, in the Guard's language."""
        return self.to_text()

    def to_text(self, language: str | None = None) -> str:
        """A short sentence for people, in ``language`` (``"en"`` or ``"es"``).

        ``"Reads branches.name and invoices.total, filtered by branch_id IN (1, 2) on invoices,
        up to 1000 rows."`` or ``"Rejected: customers.email is a hidden column. It would read
        customers.email."``
        """
        lang = language if language is not None else self.language
        if lang not in LANGUAGES:
            raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
        words = _WORDS[lang]
        reads = _join(self._read_items(words), words["and"])

        if not self.ok:
            reasons = "; ".join(violation(v.code, v.detail, lang).message for v in self.violations)
            text = words["rejected"].format(reasons=reasons)
            return f"{text} {words['would_read'].format(reads=reads)}" if reads else text

        parts = [words["reads"].format(reads=reads) if reads else words["reads_nothing"]]
        if self.row_filters:
            filters = [
                words["filter"].format(condition=condition, table=table)
                for table, condition in sorted(self.row_filters.items())
            ]
            parts.append(words["filtered"].format(filters=_join(filters, words["and"])))
        if self.limit is None:
            parts.append(words["no_limit"])
        elif self.limit == 1:
            parts.append(words["limit_one"])
        else:
            parts.append(words["limit"].format(limit=self.limit))
        return ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready dictionary, for audit logs and APIs."""
        return {
            "ok": self.ok,
            "sql": self.sql,
            "tables": sorted(self.tables),
            "columns": {table: sorted(names) for table, names in sorted(self.columns.items())},
            "unresolved_columns": sorted(self.unresolved_columns),
            "row_filters": dict(sorted(self.row_filters.items())),
            "limit": self.limit,
            "violations": [
                {"code": v.code.value, "message": v.message, "detail": v.detail}
                for v in self.violations
            ],
            "text": self.text,
        }

    def _read_items(self, words: Mapping[str, str]) -> list[str]:
        items: list[str] = []
        for table in sorted(self.tables):
            names = sorted(self.columns.get(table, ()))
            items.extend([f"{table}.{name}" for name in names] if names else [table])
        items.extend(
            words["unresolved"].format(column=name) for name in sorted(self.unresolved_columns)
        )
        return items


def _join(items: list[str], conjunction: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


@dataclass(frozen=True)
class ColumnsRead:
    """Columns per allowed table, and unqualified columns whose table is ambiguous."""

    columns: Mapping[str, frozenset[str]]
    unresolved: frozenset[str]


def read_columns(scopes: list[Scope], refs: list[PhysicalRef], compiled: Compiled) -> ColumnsRead:
    """The columns the query reads from each allowed table, in every clause and scope.

    A qualified column is resolved through the aliases visible in its scope, outer ones
    included (correlated subqueries). An unqualified column belongs to the only source of its
    ``SELECT``, or of the nearest enclosing one when it has none; with several sources it is
    unresolved. A column of a CTE or derived table is not a table column: the CTE's own scope
    records what it reads. Must run before row filters replace the tables.
    """
    dialect = compiled.dialect
    physical = {id(ref.table): str(ref.name) for ref in refs}
    found: dict[str, set[str]] = {name: set() for name in physical.values()}
    unresolved: set[str] = set()

    def record(source: object, column: str) -> None:
        table = physical.get(id(source)) if isinstance(source, exp.Table) else None
        if table is not None:
            found[table].add(column)

    for scope in scopes:
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        visible = {
            norm(alias, dialect): source for alias, source in _visible_sources(scope).items()
        }
        selected = [source for _, source in scope.selected_sources.values()]

        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                for source in selected:
                    record(source, STAR)
            elif (
                isinstance(projection, exp.Column)
                and isinstance(projection.this, exp.Star)
                and projection.table
            ):
                record(visible.get(norm(projection.args["table"], dialect)), STAR)

        aliases = {
            norm(item.alias, dialect) for item in select.expressions if isinstance(item, exp.Alias)
        }
        for column in scope_columns(scope):
            # Columns of a nested SELECT (correlated ones too) are read in that SELECT's scope.
            if not isinstance(column.this, exp.Identifier):
                continue
            if column.find_ancestor(exp.Select) is not select:
                continue
            name = norm(column.this, dialect)
            if column.table:
                record(visible.get(norm(column.args["table"], dialect)), name)
                continue
            if name in aliases and _clause(column, select) != "expressions":
                continue  # an output alias in GROUP BY, HAVING or ORDER BY
            sources = _nearest_sources(scope)
            if len(sources) == 1:
                record(sources[0], name)
            elif sources:
                unresolved.add(name)

    return ColumnsRead(
        columns=MappingProxyType({table: frozenset(names) for table, names in found.items()}),
        unresolved=frozenset(unresolved),
    )


def _nearest_sources(scope: Scope) -> list[exp.Table | Scope]:
    """Sources of the scope, or of the nearest enclosing scope that has any."""
    current: Scope | None = scope
    while current is not None:
        sources = [source for _, source in current.selected_sources.values()]
        if sources:
            return sources
        current = current.parent
    return []


def _clause(node: exp.Expression, select: exp.Select) -> str | None:
    """Which argument of ``select`` (``expressions``, ``where``…) contains ``node``."""
    current: exp.Expr = node
    while current.parent is not None and current.parent is not select:
        current = current.parent
    return current.arg_key if current.parent is select else None
