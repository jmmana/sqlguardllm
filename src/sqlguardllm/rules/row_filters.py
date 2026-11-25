"""Rule 7: row filters injected as derived tables, with context values bound as literals."""

from __future__ import annotations

import datetime
import math
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from sqlguardllm._compiled import Compiled, RowFilter
from sqlguardllm._names import TableName, find
from sqlguardllm.messages import violation
from sqlguardllm.result import PolicyError, Violation, ViolationCode
from sqlguardllm.rules.sources import PhysicalRef
from sqlguardllm.rules.statement import WRITE_NODES

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}")
_SCALARS = (bool, int, float, Decimal, str, datetime.date, datetime.datetime)
_MISSING = object()


def compile_filter(table: TableName, template: str, dialect_name: str) -> RowFilter:
    """Parse a filter template once, replacing each placeholder with a sentinel column."""
    placeholders: dict[str, str] = {}

    def to_sentinel(match: re.Match[str]) -> str:
        sentinel = f"__sqlguard_ph_{len(placeholders)}__"
        placeholders[sentinel] = match.group(1)
        return sentinel

    text = _PLACEHOLDER.sub(to_sentinel, template)
    try:
        condition = cast(exp.Expression, sqlglot.condition(text, dialect=dialect_name))
    except SqlglotError as exc:
        raise PolicyError(f"row filter for {table} is not valid SQL: {template!r}") from exc
    if condition.find(exp.Select, exp.Subquery):
        raise PolicyError(f"row filter for {table} can't contain subqueries: {template!r}")
    if condition.find(*WRITE_NODES):
        raise PolicyError(f"row filter for {table} can't contain statements: {template!r}")

    for sentinel, path in placeholders.items():
        columns = [c for c in condition.find_all(exp.Column) if c.name == sentinel]
        identifiers = [i for i in condition.find_all(exp.Identifier) if i.name == sentinel]
        if not columns or any(c.table for c in columns) or len(identifiers) != len(columns):
            raise PolicyError(
                f"placeholder {{{path}}} in the row filter for {table} must be used as a value, "
                "not inside quotes, as a name or as a function"
            )
    return RowFilter(table=table, template=template, condition=condition, placeholders=placeholders)


class _ContextError(Exception):
    def __init__(self, code: ViolationCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _resolve(context: Any, path: str) -> Any:
    current = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        else:
            current = getattr(current, part, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def _literal(value: Any, path: str) -> exp.Expression:
    if value is None:
        return exp.null()
    if not isinstance(value, _SCALARS):
        raise _ContextError(ViolationCode.INVALID_CONTEXT, f"{{{path}}} ({type(value).__name__})")
    if isinstance(value, float) and not math.isfinite(value):
        raise _ContextError(ViolationCode.INVALID_CONTEXT, f"{{{path}}} (non-finite number)")
    return cast(exp.Expression, exp.convert(value))


def bind(row_filter: RowFilter, context: Any) -> exp.Expression:
    """The filter condition with every placeholder replaced by safe literals."""
    condition = row_filter.condition.copy()
    for column in list(condition.find_all(exp.Column)):
        path = row_filter.placeholders.get(column.name)
        if path is None:
            continue
        value = _resolve(context, path)
        if value is _MISSING:
            raise _ContextError(ViolationCode.MISSING_CONTEXT, f"{{{path}}}")
        if isinstance(value, (list, tuple, set, frozenset)):
            items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else list(value)
            literals = [_literal(item, path) for item in items] or [exp.null()]
            parent = column.parent
            if not (isinstance(parent, exp.In) and column.arg_key == "expressions"):
                raise _ContextError(
                    ViolationCode.INVALID_CONTEXT,
                    f"{{{path}}} (a list can only be used inside IN (...))",
                )
            current = list(parent.expressions)
            index = next(i for i, node in enumerate(current) if node is column)
            parent.set("expressions", [*current[:index], *literals, *current[index + 1 :]])
        else:
            column.replace(_literal(value, path))
    return condition


def apply_filters(
    refs: list[PhysicalRef], compiled: Compiled, context: Any
) -> tuple[list[str], list[Violation]]:
    """Replace each filtered table occurrence with a filtered derived table."""
    if not compiled.filters:
        return [], []
    lang = compiled.language
    bound: dict[TableName, exp.Expression] = {}
    problems: list[Violation] = []
    targets: list[tuple[PhysicalRef, TableName]] = []

    for ref in refs:
        entry = find(compiled.filters.keys(), ref.name, compiled.default_schema)
        if entry is None:
            continue
        if entry not in bound:
            try:
                bound[entry] = bind(compiled.filters[entry], context)
            except _ContextError as problem:
                problems.append(violation(problem.code, problem.detail, lang))
                continue
        if ref.table.args.get("laterals") or ref.table.args.get("joins"):
            problems.append(
                violation(ViolationCode.TABLE_NOT_ALLOWED, f"{ref.name} (unsupported syntax)", lang)
            )
            continue
        targets.append((ref, entry))

    if problems:
        return [], list({(v.code, v.detail): v for v in problems}.values())

    applied: list[str] = []
    for ref, entry in targets:
        table = ref.table
        condition = bound[entry].copy()
        inner = table.copy()
        for key in ("alias", "pivots", "sample"):
            inner.set(key, None)
        alias = table.args.get("alias")
        new_alias = alias.copy() if alias is not None else exp.TableAlias(this=table.this.copy())
        derived = exp.Subquery(
            this=exp.select("*").from_(inner).where(condition),
            alias=new_alias,
        )
        if table.args.get("pivots"):
            derived.set("pivots", [p.copy() for p in table.args["pivots"]])
        table.replace(derived)
        applied.append(f"{ref.name}: {condition.sql(dialect=compiled.dialect_name)}")
    return applied, []
