"""Complexity limits: joins, nesting depth and cartesian products.

See docs/DESIGN.md#complexity-limits.

All three are opt-in. They catch queries that are far more expensive or convoluted than the
question that produced them, which is usually a model going wrong rather than an attack.
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import norm
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode

_NESTING = frozenset({ScopeType.SUBQUERY, ScopeType.DERIVED_TABLE, ScopeType.CTE})


def _depth(scope: Scope) -> int:
    """How many subqueries, derived tables and CTEs enclose this scope (itself included)."""
    depth = 0
    current: Scope | None = scope
    while current is not None:
        if current.scope_type in _NESTING:
            depth += 1
        current = current.parent
    return depth


class _Components:
    """Union-find over the sources of one SELECT."""

    def __init__(self, names: list[str]) -> None:
        self._parent = {name: name for name in names}

    def find(self, name: str) -> str:
        while self._parent[name] != name:
            self._parent[name] = self._parent[self._parent[name]]
            name = self._parent[name]
        return name

    def union(self, a: str, b: str) -> None:
        if a in self._parent and b in self._parent:
            self._parent[self.find(a)] = self.find(b)


def _source_name(node: exp.Expression, compiled: Compiled) -> str:
    return norm(node.alias_or_name, compiled.dialect)


def _unlinked_sources(select: exp.Select, compiled: Compiled) -> list[str]:
    """Sources of ``select`` that form a cartesian product with the rest."""
    from_ = select.args.get("from_") or select.args.get("from")
    joins: list[exp.Join] = select.args.get("joins") or []
    if not isinstance(from_, exp.From) or not joins:
        return []

    first = _source_name(from_.this, compiled)
    names = [first, *(_source_name(join.this, compiled) for join in joins)]
    components = _Components(names)
    crossed: list[str] = []
    for join, name in zip(joins, names[1:], strict=True):
        kind = (join.args.get("kind") or "").upper()
        if kind == "CROSS":
            crossed.append(name)
        elif join.args.get("on") or join.args.get("using") or join.args.get("method"):
            components.union(name, first)

    where = select.args.get("where")
    if isinstance(where, exp.Where):
        condition = where.this
        conjuncts = condition.flatten() if isinstance(condition, exp.And) else [condition]
        for conjunct in conjuncts:
            if not isinstance(conjunct, exp.EQ):
                continue
            left, right = conjunct.this, conjunct.expression
            if (
                isinstance(left, exp.Column)
                and isinstance(right, exp.Column)
                and left.table
                and right.table
            ):
                components.union(
                    norm(left.args["table"], compiled.dialect),
                    norm(right.args["table"], compiled.dialect),
                )

    root = components.find(first)
    unlinked = [n for n in names[1:] if n not in crossed and components.find(n) != root]
    return crossed + unlinked


def check_complexity(
    root: exp.Expression, scopes: list[Scope], compiled: Compiled
) -> list[Violation]:
    """Apply ``max_joins``, ``max_subquery_depth`` and ``forbid_cartesian``."""
    lang = compiled.language
    violations: list[Violation] = []

    if compiled.max_joins is not None:
        joins = sum(1 for _ in root.find_all(exp.Join))
        if joins > compiled.max_joins:
            detail = f"max_joins={compiled.max_joins} (found {joins})"
            violations.append(violation(ViolationCode.TOO_COMPLEX, detail, lang))

    if compiled.max_subquery_depth is not None:
        depth = max((_depth(scope) for scope in scopes), default=0)
        if depth > compiled.max_subquery_depth:
            detail = f"max_subquery_depth={compiled.max_subquery_depth} (found {depth})"
            violations.append(violation(ViolationCode.TOO_COMPLEX, detail, lang))

    if compiled.forbid_cartesian:
        for scope in scopes:
            if isinstance(scope.expression, exp.Select):
                for name in _unlinked_sources(scope.expression, compiled):
                    detail = f"forbid_cartesian ({name})"
                    violations.append(violation(ViolationCode.TOO_COMPLEX, detail, lang))

    return violations
