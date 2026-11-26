"""Masked columns: they can be selected, but come back masked.

See docs/DESIGN.md#protecting-personal-data.

A masked column may only appear in the select list. There every occurrence is replaced by its
mask, even inside functions, ``CASE`` or window clauses, so nothing computed in the select list
can see the real value. Anywhere else (``WHERE``, ``JOIN``, ``GROUP BY``, ``HAVING``, ``ORDER BY``,
``USING``, ``NATURAL JOIN``, whole-row references) it is ``hidden_column``: filtering or sorting on
the real value would leak it. ``*`` over a table with masked columns is expanded with the masks
when the policy's ``schema`` lists the table's columns, and rejected otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import TableName, find, norm, table_name
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode
from sqlguardllm.rules.columns import _visible_sources, scope_columns

_SENTINEL = "__sqlguardllm_masked__"

# SHA-256 as lowercase hex, per output dialect. SQLite has no SHA-256 function.
HASH_TEMPLATES: dict[str, str] = {
    "postgres": f"ENCODE(SHA256(CONVERT_TO(CAST({_SENTINEL} AS TEXT), 'UTF8')), 'hex')",
    "mysql": f"SHA2(CAST({_SENTINEL} AS CHAR), 256)",
    "tsql": f"LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', "
    f"CAST({_SENTINEL} AS NVARCHAR(MAX))), 2))",
    "oracle": f"LOWER(RAWTOHEX(STANDARD_HASH(TO_CHAR({_SENTINEL}), 'SHA256')))",
    "snowflake": f"SHA2(CAST({_SENTINEL} AS VARCHAR), 256)",
    "bigquery": f"TO_HEX(SHA256(CAST({_SENTINEL} AS STRING)))",
    "duckdb": f"SHA256(CAST({_SENTINEL} AS VARCHAR))",
    "trino": f"LOWER(TO_HEX(SHA256(TO_UTF8(CAST({_SENTINEL} AS VARCHAR)))))",
    "clickhouse": f"LOWER(HEX(SHA256(toString({_SENTINEL}))))",
}

_PARTIAL_VISIBLE = 2


@dataclass(frozen=True)
class _ColumnMask:
    column: exp.Column
    strategy: str


@dataclass(frozen=True)
class _StarExpansion:
    select: exp.Select
    star: exp.Expression
    sources: tuple[tuple[str, TableName], ...]  # alias, table — in FROM/JOIN order


@dataclass(frozen=True)
class MaskPlan:
    columns: tuple[_ColumnMask, ...] = ()
    stars: tuple[_StarExpansion, ...] = ()


def _masked_sources(
    sources: dict[str, exp.Expression | Scope], compiled: Compiled
) -> dict[str, tuple[TableName, dict[str, str]]]:
    found: dict[str, tuple[TableName, dict[str, str]]] = {}
    for alias, source in sources.items():
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            ref = table_name(source, compiled.dialect)
            entry = find(compiled.masked.keys(), ref, compiled.default_schema)
            if entry is not None:
                # Case-folded, like hidden columns (see rules/columns.py `_hidden_sources`).
                columns = {name.casefold(): how for name, how in compiled.masked[entry].items()}
                found[norm(alias, compiled.dialect)] = (entry, columns)
    return found


def _in_select_list(node: exp.Expression, select: exp.Expression) -> bool:
    """Whether ``node`` sits in the projections of ``select`` (not in WHERE, ORDER BY…)."""
    child: exp.Expression = node
    while child.parent is not None and child.parent is not select:
        child = cast(exp.Expression, child.parent)
    return child.parent is select and child.arg_key == "expressions"


def _ordered_sources(select: exp.Select, compiled: Compiled) -> list[tuple[str, exp.Expression]]:
    from_ = select.args.get("from_") or select.args.get("from")
    nodes = [from_.this] if isinstance(from_, exp.From) else []
    nodes += [join.this for join in select.args.get("joins") or []]
    return [(norm(node.alias_or_name, compiled.dialect), node) for node in nodes]


def _schema_for(table: exp.Expression, compiled: Compiled) -> TableName | None:
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
        return None
    ref = table_name(table, compiled.dialect)
    return find(compiled.schema.keys(), ref, compiled.default_schema)


def plan_masks(scopes: list[Scope], compiled: Compiled) -> tuple[MaskPlan, list[Violation]]:
    """Find every masked column: what to rewrite, and what to reject."""
    if not compiled.masked:
        return MaskPlan(), []
    lang = compiled.language
    details: list[str] = []
    columns: list[_ColumnMask] = []
    stars: list[_StarExpansion] = []

    for scope in scopes:
        visible = _masked_sources(_visible_sources(scope), compiled)
        if not visible:
            continue
        local = _masked_sources(dict(scope.sources), compiled)
        all_masked = {name for _, cols in visible.values() for name in cols}
        expression = scope.expression

        if isinstance(expression, exp.Select):
            for projection in expression.expressions:
                star = isinstance(projection, exp.Star)
                qualified_star = isinstance(projection, exp.Column) and isinstance(
                    projection.this, exp.Star
                )
                if not star and not qualified_star:
                    continue
                ordered = _ordered_sources(expression, compiled)
                if qualified_star:
                    qualifier = norm(projection.args["table"], compiled.dialect)
                    ordered = [(a, n) for a, n in ordered if a == qualifier]
                if not any(alias in local for alias, _ in ordered):
                    continue
                resolved = [(alias, _schema_for(node, compiled)) for alias, node in ordered]
                missing = [alias for alias, entry in resolved if entry is None]
                if missing:
                    label = "*" if star else f"{projection.table}.*"
                    tables = ", ".join(str(local[a][0]) for a, _ in ordered if a in local)
                    details.append(f"{label} over {tables} (masked columns; add it to schema)")
                    continue
                stars.append(
                    _StarExpansion(
                        expression,
                        projection,
                        tuple((alias, entry) for alias, entry in resolved if entry is not None),
                    )
                )
            for join in expression.args.get("joins") or []:
                for ident in join.args.get("using") or []:
                    if norm(ident, compiled.dialect).casefold() in all_masked:
                        details.append(f"USING ({ident.name}) on a masked column")
                if str(join.args.get("method") or "").upper() == "NATURAL":
                    details.append("NATURAL JOIN over a table with masked columns")

        for column in scope_columns(scope):
            if not isinstance(column.this, exp.Identifier):
                continue
            name = norm(column.this, compiled.dialect)
            key = name.casefold()
            strategy: str | None = None
            owner = ""
            if column.table:
                qualifier = norm(column.args["table"], compiled.dialect)
                if qualifier in visible and key in visible[qualifier][1]:
                    owner = f"{visible[qualifier][0]}.{name}"
                    strategy = visible[qualifier][1][key]
            elif key in all_masked:
                # Unqualified: mask it if any visible source masks a column with that name.
                for table, cols in visible.values():
                    if key in cols:
                        owner, strategy = f"{table}.{name}", cols[key]
                        break
            elif name in visible:
                details.append(f"{name} (whole-row reference to {visible[name][0]})")
                continue
            if strategy is None:
                continue
            if isinstance(expression, exp.Select) and _in_select_list(column, expression):
                columns.append(_ColumnMask(column, strategy))
            else:
                details.append(owner)  # masked: only allowed in the select list

    unique = list(dict.fromkeys(details))
    problems = [violation(ViolationCode.HIDDEN_COLUMN, detail, lang) for detail in unique]
    return MaskPlan(tuple(columns), tuple(stars)), problems


def mask_expression(column: exp.Expression, strategy: str, output_dialect: str) -> exp.Expression:
    """The masked value of ``column`` for ``strategy`` in ``output_dialect``."""
    if strategy == "null":
        return exp.Null()
    if strategy == "redact":
        return exp.Literal.string("***")
    if strategy == "partial":
        text = exp.cast(column.copy(), exp.DataType.build("VARCHAR(4000)"))
        visible = exp.Substring(
            this=text,
            start=exp.Literal.number(1),
            length=exp.Literal.number(_PARTIAL_VISIBLE),
        )
        return exp.Case(
            ifs=[exp.If(this=exp.Is(this=column.copy(), expression=exp.Null()), true=exp.Null())],
            default=exp.Concat(expressions=[visible, exp.Literal.string("***")]),
        )
    template = cast(
        exp.Expression, sqlglot.parse_one(HASH_TEMPLATES[output_dialect], read=output_dialect)
    )
    for node in list(template.find_all(exp.Column)):
        if node.name == _SENTINEL:
            node.replace(column.copy())
    return template


def apply_masks(plan: MaskPlan, compiled: Compiled) -> None:
    """Rewrite the planned columns and stars in place."""
    out = compiled.output_dialect_name
    for expansion in plan.stars:
        replacement: list[exp.Expression] = []
        for alias, entry in expansion.sources:
            strategies = next(
                (
                    {name.casefold(): how for name, how in cols.items()}
                    for table, cols in compiled.masked.items()
                    if table == entry
                ),
                {},
            )
            for name in compiled.schema[entry]:
                column = exp.column(name, table=alias)
                if name.casefold() in strategies:
                    masked = mask_expression(column, strategies[name.casefold()], out)
                    replacement.append(cast(exp.Expression, exp.alias_(masked, name)))
                else:
                    replacement.append(column)
        projections = list(expansion.select.expressions)
        index = next(i for i, p in enumerate(projections) if p is expansion.star)
        projections[index : index + 1] = replacement
        expansion.select.set("expressions", projections)

    for item in plan.columns:
        column = item.column
        masked = mask_expression(column, item.strategy, out)
        if isinstance(column.parent, exp.Select) and column.arg_key == "expressions":
            column.replace(exp.alias_(masked, column.name))  # keep the output name
        else:
            column.replace(masked)
