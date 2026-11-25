"""Rules 2 and 3: one statement, a query root, and no write anywhere in the tree."""

from __future__ import annotations

from sqlglot import exp

from sqlguard.messages import violation
from sqlguard.result import Violation, ViolationCode

_WRITE_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Into",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Copy",
    "Grant",
    "Revoke",
    "Command",
    "Execute",
    "Attach",
    "Detach",
    "Pragma",
    "Set",
    "Use",
    "Transaction",
    "Commit",
    "Rollback",
    "Kill",
    "LoadData",
    "Refresh",
    "Cache",
    "Uncache",
    "Analyze",
)

WRITE_NODES: tuple[type[exp.Expression], ...] = tuple(
    node
    for node in (getattr(exp, name, None) for name in _WRITE_NODE_NAMES)
    if isinstance(node, type) and issubclass(node, exp.Expression)
)

QUERY_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, (exp.Subquery, exp.Paren)) and isinstance(node.this, exp.Expression):
        node = node.this
    return node


def describe(node: exp.Expression) -> str:
    """A short, readable name for a statement or node (``DELETE``, ``SELECT ... INTO``)."""
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    if isinstance(node, exp.Into):
        return "SELECT ... INTO"
    name = type(node).__name__
    return {"TruncateTable": "TRUNCATE", "LoadData": "LOAD DATA"}.get(name, name.upper())


def check_statements(
    statements: list[exp.Expression], language: str
) -> tuple[exp.Expression | None, list[Violation]]:
    """Validate the statements; return the query root when there is exactly one valid query."""
    violations: list[Violation] = []
    if not statements:
        return None, [violation(ViolationCode.EMPTY_STATEMENT, "", language)]
    if len(statements) > 1:
        violations.append(
            violation(ViolationCode.MULTIPLE_STATEMENTS, f"{len(statements)} statements", language)
        )

    for statement in statements:
        root = _unwrap(statement)
        if not isinstance(root, QUERY_ROOTS):
            violations.append(violation(ViolationCode.NOT_A_QUERY, describe(root), language))
        for node in statement.walk():
            if node is root:
                continue
            if isinstance(node, exp.Lock):
                violations.append(
                    violation(ViolationCode.ROW_LOCK, node.sql()[:60] or "FOR UPDATE", language)
                )
            elif isinstance(node, WRITE_NODES):
                violations.append(
                    violation(ViolationCode.WRITE_OPERATION, describe(node), language)
                )

    if violations:
        return None, violations
    return _unwrap(statements[0]), []
