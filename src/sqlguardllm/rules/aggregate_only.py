"""Aggregate-only tables with a minimum group size (k-anonymity).

The ``SELECT`` that reads such a table may only project aggregates and ``GROUP BY`` expressions,
and the guard adds ``HAVING COUNT(*) >= k`` so no group smaller than ``k`` rows comes back.
Window functions don't count as aggregates: ``AVG(x) OVER ()`` still returns one row per person.

Known limits, documented in docs/DESIGN.md: ``COUNT(*)`` counts rows, so a join that repeats a
person counts them more than once; and k-anonymity on its own doesn't stop differencing attacks
(asking for a group with and without one person).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlglot import exp

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import find
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode
from sqlguardllm.rules.sources import PhysicalRef


@dataclass(frozen=True)
class AggregatePlan:
    selects: tuple[tuple[exp.Select, int], ...] = ()


def _is_aggregated(column: exp.Column, select: exp.Select) -> bool:
    """Inside an aggregate function of ``select`` that is not a window function."""
    node = cast(exp.Expression | None, column.parent)
    while node is not None and node is not select:
        if isinstance(node, exp.Window):
            return False
        if isinstance(node, exp.AggFunc):
            return not isinstance(node.parent, exp.Window)
        node = cast(exp.Expression | None, node.parent)
    return False


def _projections_ok(select: exp.Select) -> bool:
    group = select.args.get("group")
    items = list(group.expressions) if isinstance(group, exp.Group) else []
    grouped = {e.sql() for e in items}
    positions = {int(e.name) for e in items if isinstance(e, exp.Literal) and e.is_int}
    for index, projection in enumerate(select.expressions, start=1):
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(target, exp.Star) or (
            isinstance(target, exp.Column) and isinstance(target.this, exp.Star)
        ):
            return False
        alias = projection.alias if isinstance(projection, exp.Alias) else ""
        if index in positions or target.sql() in grouped or (alias and alias in grouped):
            continue
        for column in target.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select:
                continue  # a nested subquery: its own scope is checked on its own
            if not _is_aggregated(column, select) and column.sql() not in grouped:
                return False
        if any(isinstance(w, exp.Window) for w in target.find_all(exp.Window)):
            return False
    return True


def check_aggregate_only(
    refs: list[PhysicalRef], compiled: Compiled
) -> tuple[AggregatePlan, list[Violation]]:
    if not compiled.aggregate_only:
        return AggregatePlan(), []
    plan: dict[int, tuple[exp.Select, int]] = {}
    details: list[str] = []
    for ref in refs:
        entry = find(compiled.aggregate_only.keys(), ref.name, compiled.default_schema)
        if entry is None:
            continue
        select = ref.table.find_ancestor(exp.Select)
        if select is None or not _projections_ok(select):
            details.append(str(ref.name))
            continue
        k = compiled.aggregate_only[entry]
        current = plan.get(id(select))
        plan[id(select)] = (select, max(k, current[1]) if current else k)
    problems = [
        violation(ViolationCode.AGGREGATE_REQUIRED, detail, compiled.language)
        for detail in dict.fromkeys(details)
    ]
    return AggregatePlan(tuple(plan.values())), problems


def apply_aggregate_only(plan: AggregatePlan) -> None:
    """Add ``HAVING COUNT(*) >= k`` to every planned ``SELECT``, keeping any existing HAVING."""
    for select, k in plan.selects:
        condition: exp.Expression = exp.GTE(
            this=exp.Count(this=exp.Star()), expression=exp.Literal.number(k)
        )
        having = select.args.get("having")
        if isinstance(having, exp.Having):
            condition = cast(exp.Expression, exp.and_(exp.paren(having.this.copy()), condition))
        select.set("having", exp.Having(this=condition))
