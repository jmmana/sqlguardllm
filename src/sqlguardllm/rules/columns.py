"""Rule 5: hidden columns can't be referenced anywhere, nor reached through ``*``."""

from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import TableName, find, norm, table_name
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode


def _visible_sources(scope: Scope) -> dict[str, exp.Expression | Scope]:
    """Sources of a scope plus those of enclosing scopes (correlated references)."""
    chain: list[Scope] = []
    current: Scope | None = scope
    while current is not None:
        chain.append(current)
        current = current.parent
    merged: dict[str, exp.Expression | Scope] = {}
    for item in reversed(chain):
        merged.update(item.sources)
    return merged


def _hidden_sources(
    sources: dict[str, exp.Expression | Scope], compiled: Compiled
) -> dict[str, tuple[TableName, frozenset[str]]]:
    found: dict[str, tuple[TableName, frozenset[str]]] = {}
    for alias, source in sources.items():
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            entry = find(
                compiled.hidden.keys(),
                table_name(source, compiled.dialect),
                compiled.default_schema,
            )
            if entry is not None:
                found[norm(alias, compiled.dialect)] = (entry, compiled.hidden[entry])
    return found


def check_columns(scopes: list[Scope], compiled: Compiled) -> list[Violation]:
    """Reject references to hidden columns, conservatively when a column is unqualified."""
    if not compiled.hidden:
        return []
    lang = compiled.language
    details: list[str] = []

    for scope in scopes:
        visible = _hidden_sources(_visible_sources(scope), compiled)
        if not visible:
            continue
        local = _hidden_sources(dict(scope.sources), compiled)
        all_hidden = frozenset().union(*(cols for _, cols in visible.values()))
        expression = scope.expression

        if isinstance(expression, exp.Select):
            for projection in expression.expressions:
                if isinstance(projection, exp.Star) and local:
                    details.append(f"* over {', '.join(str(t) for t, _ in local.values())}")
                elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                    qualifier = (
                        norm(projection.args["table"], compiled.dialect) if projection.table else ""
                    )
                    if qualifier in visible:
                        details.append(f"{projection.table}.*")
            for join in expression.args.get("joins") or []:
                for ident in join.args.get("using") or []:
                    if norm(ident, compiled.dialect) in all_hidden:
                        details.append(f"USING ({ident.name})")
                if str(join.args.get("method") or "").upper() == "NATURAL":
                    details.append("NATURAL JOIN over a table with hidden columns")

        for column in scope.columns:
            if not isinstance(column.this, exp.Identifier):
                continue
            name = norm(column.this, compiled.dialect)
            if column.table:
                qualifier = norm(column.args["table"], compiled.dialect)
                if qualifier in visible and name in visible[qualifier][1]:
                    details.append(f"{visible[qualifier][0]}.{name}")
            elif name in all_hidden:
                owners = [str(t) for t, cols in visible.values() if name in cols]
                details.append(f"{owners[0]}.{name}" if owners else name)
            elif name in visible:
                details.append(f"{name} (whole-row reference to {visible[name][0]})")

    unique = list(dict.fromkeys(details))
    return [violation(ViolationCode.HIDDEN_COLUMN, detail, lang) for detail in unique]
