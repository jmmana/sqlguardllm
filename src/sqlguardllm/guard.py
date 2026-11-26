"""The Guard: compile a policy once, then check and rewrite SQL."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from sqlguardllm._compiled import Compiled, RowFilter
from sqlguardllm._names import TableName, find, norm, parse_column_entry, parse_table_entry
from sqlguardllm.data.denied_functions import default_denylist
from sqlguardllm.explain import Explanation, protections, read_columns
from sqlguardllm.messages import LANGUAGES, violation
from sqlguardllm.policy import Policy
from sqlguardllm.result import CheckResult, GuardViolation, PolicyError, Violation, ViolationCode
from sqlguardllm.rules.aggregate_only import (
    AggregatePlan,
    apply_aggregate_only,
    check_aggregate_only,
)
from sqlguardllm.rules.columns import check_columns
from sqlguardllm.rules.complexity import check_complexity
from sqlguardllm.rules.functions import check_functions
from sqlguardllm.rules.limit import apply_limit, current_limit
from sqlguardllm.rules.masking import HASH_TEMPLATES, MaskPlan, apply_masks, plan_masks
from sqlguardllm.rules.required_filters import check_required_filters
from sqlguardllm.rules.row_filters import apply_filters, compile_filter
from sqlguardllm.rules.sources import PhysicalRef, collect_sources
from sqlguardllm.rules.statement import check_statements

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
    output_name = (policy.output_dialect or dialect_name).lower()
    try:
        Dialect.get_or_raise(output_name)
    except (ValueError, SqlglotError) as exc:
        raise PolicyError(f"unknown output_dialect {policy.output_dialect!r}") from exc

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

    required: dict[TableName, frozenset[str]] = {}
    for key, columns in sorted(policy.required_filters.items()):
        table = parse_table_entry(key, dialect, dialect_name)
        if tables is not None and find(tables, table, default_schema) is None:
            raise PolicyError(f"required filter for {key!r}, which is not in the allowed tables")
        required[table] = frozenset(norm(column, dialect) for column in columns)

    masked: dict[TableName, dict[str, str]] = {}
    for entry, strategy in sorted(policy.masked_columns.items()):
        table, column = parse_column_entry(entry, dialect, dialect_name)
        if tables is not None and find(tables, table, default_schema) is None:
            raise PolicyError(f"masked column {entry!r} is on a table outside the allowed tables")
        if table in hidden and column in hidden[table]:
            raise PolicyError(f"a column can't be both hidden and masked: {entry!r}")
        if strategy == "hash" and output_name not in HASH_TEMPLATES:
            raise PolicyError(
                f"the hash mask is not available for {output_name}; use null, redact or partial"
            )
        masked.setdefault(table, {})[column] = strategy

    aggregate_only: dict[TableName, int] = {}
    for key, k in sorted(policy.aggregate_only.items()):
        table = parse_table_entry(key, dialect, dialect_name)
        if tables is not None and find(tables, table, default_schema) is None:
            raise PolicyError(f"aggregate_only for {key!r}, which is not in the allowed tables")
        aggregate_only[table] = k

    schema: dict[TableName, tuple[str, ...]] = {}
    for key, columns in sorted((policy.schema or {}).items()):
        schema[parse_table_entry(key, dialect, dialect_name)] = tuple(
            norm(column, dialect) for column in columns
        )

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
        output_dialect_name=output_name,
        max_joins=policy.max_joins,
        max_subquery_depth=policy.max_subquery_depth,
        forbid_cartesian=policy.forbid_cartesian,
        required=required,
        masked=masked,
        aggregate_only=aggregate_only,
        schema=schema,
    )


@dataclass
class _State:
    """One pass of the pipeline over a query, shared by ``check()`` and ``explain()``."""

    violations: list[Violation]
    root: exp.Expression | None = None
    scopes: list[Scope] = field(default_factory=list)
    refs: list[PhysicalRef] = field(default_factory=list)
    masks: MaskPlan = field(default_factory=MaskPlan)
    aggregates: AggregatePlan = field(default_factory=AggregatePlan)
    filters: list[tuple[str, str]] = field(default_factory=list)  # (table, bound condition)
    sql: str | None = None


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
        state = self._analyze(sql)
        self._rewrite(state, context)
        return self._result(state)

    def explain(self, sql: str, context: Mapping[str, Any] | None = None) -> Explanation:
        """Describe what ``sql`` reads and what the guard does to it, allowed or not.

        Runs the same pass as :meth:`check`, so both always agree, and adds the columns read
        per table, the masks and minimum group sizes that apply. The explanation's text is in
        the Guard's language and includes the bound row filters, so it is meant for users, UIs
        and audit logs rather than for the model.

        Args:
            sql: The SQL written by the model, agent or user.
            context: Values for row-filter placeholders, as in :meth:`check`.
        """
        state = self._analyze(sql)
        # Read the columns before the rewrite replaces filtered tables with derived tables.
        reads = read_columns(state.scopes, state.refs, self._compiled)
        masked, groups = protections(reads, state.refs, self._compiled)
        self._rewrite(state, context)
        result = self._result(state)
        return Explanation(
            ok=result.ok,
            sql=result.sql,
            tables=frozenset(str(ref.name) for ref in state.refs),
            columns=reads.columns,
            unresolved_columns=reads.unresolved,
            masked_columns=masked if result.ok else {},
            aggregate_only=groups if result.ok else {},
            row_filters=dict(state.filters),
            limit=current_limit(state.root) if result.ok and state.root is not None else None,
            violations=result.violations,
            language=self._compiled.language,
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

    def _analyze(self, sql: str) -> _State:
        """Steps 1-6: parse the query and collect every violation; nothing is rewritten."""
        if not isinstance(sql, str):
            raise TypeError("sql must be a string")
        compiled = self._compiled
        lang = compiled.language

        try:
            parsed = sqlglot.parse(sql, read=compiled.dialect_name)
        except SqlglotError as exc:
            return _State([violation(ViolationCode.PARSE_ERROR, _first_line(exc), lang)])
        statements = [
            cast(exp.Expression, statement) for statement in parsed if statement is not None
        ]

        root, violations = check_statements(statements, lang)
        if root is None:
            return _State(violations)

        try:
            scopes = traverse_scope(root)
        except SqlglotError as exc:
            return _State([violation(ViolationCode.PARSE_ERROR, _first_line(exc), lang)])

        refs, problems = collect_sources(root, scopes, compiled)
        problems += check_columns(scopes, compiled)
        problems += check_functions(root, compiled)
        problems += check_complexity(root, scopes, compiled)
        problems += check_required_filters(refs, compiled)
        masks, mask_problems = plan_masks(scopes, compiled)
        problems += mask_problems
        aggregates, aggregate_problems = check_aggregate_only(refs, compiled)
        problems += aggregate_problems
        return _State(
            problems, root=root, scopes=scopes, refs=refs, masks=masks, aggregates=aggregates
        )

    def _rewrite(self, state: _State, context: Mapping[str, Any] | None) -> None:
        """Steps 7-9, only for a query without violations: masks, row filters, limit, generate."""
        if state.violations or state.root is None:
            return
        compiled = self._compiled
        apply_masks(state.masks, compiled)
        apply_aggregate_only(state.aggregates)
        applied, problems = apply_filters(
            state.refs, compiled, context if context is not None else {}
        )
        if problems:
            state.violations = problems
            return
        state.filters = applied
        state.root = apply_limit(state.root, compiled.max_rows)
        state.sql = state.root.sql(dialect=compiled.output_dialect_name, pretty=self._pretty)

    @staticmethod
    def _result(state: _State) -> CheckResult:
        if state.violations or state.sql is None:
            unique = tuple({(v.code, v.detail): v for v in state.violations}.values())
            return CheckResult(ok=False, violations=unique)
        return CheckResult(
            ok=True,
            sql=state.sql,
            tables=frozenset(str(ref.name) for ref in state.refs),
            applied_filters=tuple(f"{table}: {condition}" for table, condition in state.filters),
        )
