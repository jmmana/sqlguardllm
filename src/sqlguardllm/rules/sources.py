"""Rule 4: every physical table must be allowlisted; table functions are rejected."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from sqlguardllm._compiled import Compiled
from sqlguardllm._names import TableName, find, table_name
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode

_DUAL_DIALECTS = frozenset({"oracle", "mysql"})


@dataclass(frozen=True)
class PhysicalRef:
    """A reference to an allowlisted physical table, in the scope where it appears."""

    table: exp.Table
    name: TableName
    scope: Scope


def collect_sources(
    root: exp.Expression, scopes: list[Scope], compiled: Compiled
) -> tuple[list[PhysicalRef], list[Violation]]:
    """Classify every table reference: CTE, derived table, allowed table, or violation."""
    lang = compiled.language
    refs: list[PhysicalRef] = []
    violations: list[Violation] = []
    handled: set[int] = set()

    for scope in scopes:
        for table in scope.tables:
            handled.add(id(table))
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope):
                continue  # a CTE or derived table: its own scope is checked separately
            if not isinstance(table.this, exp.Identifier):
                fragment = table.this.sql(dialect=compiled.dialect_name)[:80]
                violations.append(violation(ViolationCode.TABLE_NOT_ALLOWED, fragment, lang))
                continue
            name = table_name(table, compiled.dialect)
            if (
                name.name.lower() == "dual"
                and name.db is None
                and compiled.dialect_name in _DUAL_DIALECTS
            ):
                continue
            if (
                compiled.tables is not None
                and find(compiled.tables, name, compiled.default_schema) is None
            ):
                violations.append(violation(ViolationCode.TABLE_NOT_ALLOWED, str(name), lang))
                continue
            refs.append(PhysicalRef(table=table, name=name, scope=scope))

    # Fail closed: any table node the scope analysis didn't classify is rejected.
    for table in root.find_all(exp.Table):
        if id(table) not in handled:
            fragment = table.sql(dialect=compiled.dialect_name)[:80]
            violations.append(violation(ViolationCode.TABLE_NOT_ALLOWED, fragment, lang))

    return refs, violations
