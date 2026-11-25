"""Required filters: some tables can only be read with a predicate on given columns.

The typical use is a date range on a large fact table, so a model can't scan all of it. Every
occurrence of the table (in the main query, subqueries and CTEs) needs, in its own ``WHERE`` or
in the ``JOIN … ON``/``USING`` that brings it in, a predicate that references one of the columns.
The check runs on the query as written, before row filters are injected.
"""

from __future__ import annotations

from sqlglot import exp

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import find, norm
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode
from sqlguardllm.rules.sources import PhysicalRef


def _predicates(ref: PhysicalRef) -> tuple[exp.Select | None, list[exp.Expression]]:
    """The SELECT that reads ``ref`` and the predicates that can filter it there."""
    select = ref.table.find_ancestor(exp.Select)
    if select is None:
        return None, []
    predicates: list[exp.Expression] = []
    where = select.args.get("where")
    if isinstance(where, exp.Where):
        predicates.append(where.this)
    join = ref.table.parent
    if isinstance(join, exp.Join):
        if isinstance(join.args.get("on"), exp.Expression):
            predicates.append(join.args["on"])
        predicates.extend(join.args.get("using") or [])
    return select, predicates


def _is_filtered(ref: PhysicalRef, columns: frozenset[str], compiled: Compiled) -> bool:
    select, predicates = _predicates(ref)
    if select is None:
        return False
    alias = norm(ref.table.alias_or_name, compiled.dialect)
    for predicate in predicates:
        nodes: list[exp.Expression] = [predicate] if isinstance(predicate, exp.Identifier) else []
        nodes += list(predicate.find_all(exp.Column))
        for node in nodes:
            if isinstance(node, exp.Column):
                # Columns of a nested subquery belong to that subquery, not to this read.
                if node.find_ancestor(exp.Select) is not select:
                    continue
                qualifier = node.args.get("table")
                if qualifier is not None and norm(qualifier, compiled.dialect) != alias:
                    continue
                name = norm(node.this, compiled.dialect)
            else:
                name = norm(node, compiled.dialect)
            if name in columns:
                return True
    return False


def check_required_filters(refs: list[PhysicalRef], compiled: Compiled) -> list[Violation]:
    """Reject every read of a table in ``required_filters`` that lacks its filter."""
    if not compiled.required:
        return []
    violations: list[Violation] = []
    for ref in refs:
        entry = find(compiled.required, ref.name, compiled.default_schema)
        if entry is None:
            continue
        columns = compiled.required[entry]
        if not _is_filtered(ref, columns, compiled):
            detail = f"{ref.name} [{', '.join(sorted(columns))}]"
            violations.append(
                violation(ViolationCode.MISSING_REQUIRED_FILTER, detail, compiled.language)
            )
    return violations
