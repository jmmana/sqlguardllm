"""Results, violation codes and errors of sqlguardllm."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ViolationCode(str, Enum):
    """Stable reasons why a query is rejected. Codes never change within a major version."""

    PARSE_ERROR = "parse_error"
    EMPTY_STATEMENT = "empty_statement"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_A_QUERY = "not_a_query"
    WRITE_OPERATION = "write_operation"
    ROW_LOCK = "row_lock"
    TABLE_NOT_ALLOWED = "table_not_allowed"
    HIDDEN_COLUMN = "hidden_column"
    DENIED_FUNCTION = "denied_function"
    MISSING_CONTEXT = "missing_context"
    INVALID_CONTEXT = "invalid_context"
    TOO_COMPLEX = "too_complex"
    MISSING_REQUIRED_FILTER = "missing_required_filter"
    AGGREGATE_REQUIRED = "aggregate_required"
    AUDIT_FAILURE = "audit_failure"


@dataclass(frozen=True)
class Violation:
    """One reason a query was rejected.

    Attributes:
        code: Stable machine-readable reason.
        message: Human-readable explanation, safe to show to a model or a user.
        detail: The offending SQL fragment or name, when there is one.
    """

    code: ViolationCode
    message: str
    detail: str = ""


@dataclass(frozen=True)
class CheckResult:
    """Outcome of :meth:`sqlguardllm.Guard.check`.

    Attributes:
        ok: True when the query may run.
        sql: The rewritten SQL to execute (only when ``ok``).
        tables: Physical tables the query reads, as normalized dotted names.
        applied_filters: Row filters injected, one entry per filtered table occurrence.
        violations: Every reason the query was rejected (empty when ``ok``).
    """

    ok: bool
    sql: str | None = None
    tables: frozenset[str] = frozenset()
    applied_filters: tuple[str, ...] = ()
    violations: tuple[Violation, ...] = field(default_factory=tuple)

    @property
    def codes(self) -> frozenset[ViolationCode]:
        """The set of violation codes, handy for assertions and branching."""
        return frozenset(v.code for v in self.violations)

    @property
    def messages(self) -> list[str]:
        """Human-readable messages of every violation."""
        return [v.message for v in self.violations]


class PolicyError(ValueError):
    """The policy itself is invalid. Raised when a :class:`~sqlguardllm.Guard` is created."""


class GuardViolation(Exception):
    """Raised by :meth:`sqlguardllm.Guard.enforce` when a query is rejected."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))
