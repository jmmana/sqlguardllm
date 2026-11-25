"""The Guard: compile a policy once, then check and rewrite SQL."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from sqlguard._compiled import Compiled, RowFilter
from sqlguard._names import TableName, find, norm, parse_column_entry, parse_table_entry
from sqlguard.data.denied_functions import default_denylist
from sqlguard.messages import LANGUAGES, violation
from sqlguard.policy import Policy
from sqlguard.result import CheckResult, GuardViolation, PolicyError, Violation, ViolationCode
from sqlguard.rules.columns import check_columns
from sqlguard.rules.functions import check_functions
from sqlguard.rules.limit import apply_limit
from sqlguard.rules.row_filters import apply_filters, compile_filter
from sqlguard.rules.sources import collect_sources
from sqlguard.rules.statement import check_statements

R = TypeVar("R")


def _first_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:200]


def _compile(policy: Policy, language: str) -> Compiled:
    if language not in LANGUAGES:
        raise PolicyError(f"language must be one of {', '.join(LANGUAGES)}")
    dialect_name = policy.dialect.lower()
    try:
        dialect = Dialect.get_or_raise(dialect_name)
    except (ValueError, SqlglotError) as exc:
        raise PolicyError(f"unknown dialect {policy.dialect!r}") from exc

    tables = (
        tuple(parse_table_entry(t, dialect, dialect_name) for t in sorted(policy.tables))
        if policy.tables is not None
        else None
    )
    default_schema = norm(policy.default_schema, dialect) if policy.default_schema else None

    hidden: dict[TableName, set[str]] = {}
    for entry in sorted(policy.hidden_columns):
        table, column = parse_column_entry(entry, dialect, dialect_name)
        hidden.setdefault(table, set()).add(column)

    filters: dict[TableName, RowFilter] = {}
    for key, template in sorted(policy.row_filters.items()):
        table = parse_table_entry(key, dialect, dialect_name)
        if tables is not None and find(tables, table, default_schema) is None:
            raise PolicyError(f"row filter for {key!r}, which is not in the allowed tables")
        filters[table] = compile_filter(table, template, dialect_name)

    base = (
        policy.denied_functions
        if policy.denied_functions is not None
        else default_denylist(dialect_name)
    )
    denied = frozenset(name.lower() for name in (*base, *policy.extra_denied_functions))
    allowed = (
        frozenset(name.lower() for name in policy.allowed_functions)
        if policy.function_policy == "allowlist" and policy.allowed_functions is not None
        else None
    )
    return Compiled(
        dialect=dialect,
        dialect_name=dialect_name,
        language=language,
        tables=tables,
        hidden={table: frozenset(columns) for table, columns in hidden.items()},
        filters=filters,
        denied=denied,
        allowed=allowed,
        max_rows=policy.max_rows,
        default_schema=default_schema,
    )


class Guard:
    """Validates and rewrites SQL written by an untrusted source, following a :class:`Policy`.

    Args:
        policy: What queries may read.
        language: Language of violation messages, ``"en"`` (default) or ``"es"``.
        pretty: Pretty-print the rewritten SQL.

    Raises:
        PolicyError: If the policy is invalid (checked once, here).
    """

    def __init__(self, policy: Policy, *, language: str = "en", pretty: bool = False) -> None:
        self.policy = policy
        self._compiled = _compile(policy, language)
        self._pretty = pretty

    def check(self, sql: str, context: Mapping[str, Any] | None = None) -> CheckResult:
        """Validate ``sql`` and, when it is allowed, return the rewritten SQL to execute.

        Args:
            sql: The SQL written by the model, agent or user.
            context: Values for row-filter placeholders, from the authenticated user, never
                from the model's output. ``{"user": {"branches": [1, 2]}}`` fills
                ``{user.branches}``.
        """
        if not isinstance(sql, str):
            raise TypeError("sql must be a string")
        compiled = self._compiled
        lang = compiled.language

        try:
            parsed = sqlglot.parse(sql, read=compiled.dialect_name)
        except SqlglotError as exc:
            return self._rejected([violation(ViolationCode.PARSE_ERROR, _first_line(exc), lang)])
        statements = [
            cast(exp.Expression, statement) for statement in parsed if statement is not None
        ]

        root, violations = check_statements(statements, lang)
        if root is None:
            return self._rejected(violations)

        try:
            scopes = traverse_scope(root)
        except SqlglotError as exc:
            return self._rejected([violation(ViolationCode.PARSE_ERROR, _first_line(exc), lang)])

        refs, problems = collect_sources(root, scopes, compiled)
        problems += check_columns(scopes, compiled)
        problems += check_functions(root, compiled)
        if problems:
            return self._rejected(problems)

        applied, problems = apply_filters(refs, compiled, context if context is not None else {})
        if problems:
            return self._rejected(problems)

        root = apply_limit(root, compiled.max_rows)
        return CheckResult(
            ok=True,
            sql=root.sql(dialect=compiled.dialect_name, pretty=self._pretty),
            tables=frozenset(str(ref.name) for ref in refs),
            applied_filters=tuple(applied),
        )

    def enforce(self, sql: str, context: Mapping[str, Any] | None = None) -> str:
        """Like :meth:`check`, but return the safe SQL or raise :class:`GuardViolation`."""
        result = self.check(sql, context)
        if not result.ok or result.sql is None:
            raise GuardViolation(result.violations)
        return result.sql

    def wrap(
        self,
        execute: Callable[..., R],
        context_fn: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> Callable[..., R]:
        """Wrap a function whose first argument is SQL so it only ever receives safe SQL.

        ``context_fn`` is called on every call to get the current user's context.
        """

        @functools.wraps(execute)
        def guarded(sql: str, *args: Any, **kwargs: Any) -> R:
            context = context_fn() if context_fn is not None else None
            return execute(self.enforce(sql, context), *args, **kwargs)

        return guarded

    @staticmethod
    def _rejected(violations: list[Violation]) -> CheckResult:
        unique = tuple({(v.code, v.detail): v for v in violations}.values())
        return CheckResult(ok=False, violations=unique)
