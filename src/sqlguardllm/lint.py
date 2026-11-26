"""Warnings for policy configurations that are valid but may be unsafe."""

from __future__ import annotations

from dataclasses import dataclass

from sqlguardllm.data.denied_functions import default_denylist
from sqlguardllm.policy import Policy

MAX_ROWS_WARNING_THRESHOLD = 10_000
MIN_AGGREGATE_K = 5

_TENANT_COLUMNS = {
    "tenant",
    "tenant_id",
    "tenantid",
    "company_id",
    "companyid",
    "branch_id",
    "branchid",
    "sucursal",
    "org_id",
    "orgid",
    "organization_id",
    "organizationid",
}
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "ssn",
    "cedula",
    "email",
    "phone",
    "card",
)


@dataclass(frozen=True)
class LintWarning:
    code: str
    message: str
    fix: str


@dataclass(frozen=True)
class SkippedCheck:
    code: str
    reason: str


@dataclass(frozen=True)
class SuppressedWarning:
    code: str
    reason: str


@dataclass(frozen=True)
class LintResult:
    warnings: tuple[LintWarning, ...]
    skipped: tuple[SkippedCheck, ...]
    suppressed: tuple[SuppressedWarning, ...]


def _normalized_table(name: str, default_schema: str | None) -> str:
    lowered = name.casefold()
    if "." not in lowered and default_schema:
        return f"{default_schema.casefold()}.{lowered}"
    return lowered


def lint_policy(policy: Policy) -> LintResult:
    """Return actionable warnings and checks that could not be performed."""
    warnings: list[LintWarning] = []
    skipped: list[SkippedCheck] = []

    if not policy.schema:
        reason = "policy schema is missing; provide schema: {table: [columns]} to inspect columns"
        skipped.extend(
            (
                SkippedCheck("tenant_filter_missing", reason),
                SkippedCheck("sensitive_column_unprotected", reason),
            )
        )
    else:
        filtered_tables = {
            _normalized_table(table, policy.default_schema) for table in policy.row_filters
        }
        protected_columns = {
            f"{_normalized_table(table, policy.default_schema)}.{column.casefold()}"
            for entry in (*policy.hidden_columns, *policy.masked_columns)
            for table, _, column in [entry.rpartition(".")]
        }
        for table, columns in policy.schema.items():
            normalized_table = _normalized_table(table, policy.default_schema)
            for column in columns:
                lowered = column.casefold()
                if lowered in _TENANT_COLUMNS and normalized_table not in filtered_tables:
                    warnings.append(
                        LintWarning(
                            "tenant_filter_missing",
                            f"table {table!r} has tenant-like column {column!r} but no row filter",
                            f"add a row_filters entry for {table} using the authenticated tenant "
                            "or organization context",
                        )
                    )
                if (
                    any(part in lowered for part in _SENSITIVE_PARTS)
                    and f"{normalized_table}.{lowered}" not in protected_columns
                ):
                    warnings.append(
                        LintWarning(
                            "sensitive_column_unprotected",
                            f"column {table}.{column} looks sensitive but is neither hidden "
                            "nor masked",
                            f"add {table}.{column} to hidden_columns or masked_columns",
                        )
                    )

    if policy.max_rows is None or policy.max_rows > MAX_ROWS_WARNING_THRESHOLD:
        message = (
            "max_rows is unset"
            if policy.max_rows is None
            else f"max_rows is {policy.max_rows}, above the recommended "
            f"{MAX_ROWS_WARNING_THRESHOLD}"
        )
        warnings.append(
            LintWarning(
                "max_rows_weak",
                message,
                f"set max_rows to {MAX_ROWS_WARNING_THRESHOLD} or less",
            )
        )

    for table, k in policy.aggregate_only.items():
        if k < MIN_AGGREGATE_K:
            warnings.append(
                LintWarning(
                    "aggregate_k_low",
                    f"aggregate_only for {table!r} uses k={k}, below the recommended "
                    f"{MIN_AGGREGATE_K}",
                    f"set aggregate_only.{table} to at least {MIN_AGGREGATE_K}",
                )
            )

    defaults = default_denylist(policy.dialect.casefold())
    reallowed: set[str] = set()
    if policy.function_policy == "allowlist":
        allowed = {name.casefold() for name in (policy.allowed_functions or ())}
        reallowed.update(defaults & allowed)
    elif policy.denied_functions is not None:
        denied = {
            name.casefold() for name in (*policy.denied_functions, *policy.extra_denied_functions)
        }
        reallowed.update(defaults - denied)
    if reallowed:
        names = ", ".join(sorted(reallowed))
        warnings.append(
            LintWarning(
                "default_denylist_reduced",
                f"functions from the built-in denylist are allowed: {names}",
                "keep these functions denied; remove them from allowed_functions or restore them "
                "in denied_functions",
            )
        )

    known_codes = {warning.code for warning in warnings}
    suppressed: list[SuppressedWarning] = []
    remaining: list[LintWarning] = []
    for warning in warnings:
        suppression_reason = policy.lint_suppressions.get(warning.code)
        if suppression_reason:
            if not any(item.code == warning.code for item in suppressed):
                suppressed.append(SuppressedWarning(warning.code, suppression_reason))
        else:
            remaining.append(warning)
    for code, _reason in policy.lint_suppressions.items():
        if code not in known_codes:
            remaining.append(
                LintWarning(
                    "unknown_suppression",
                    f"lint_suppressions contains unknown or inactive warning code {code!r}",
                    "remove the entry or use a warning code emitted by sqlguardllm lint",
                )
            )
            break
    return LintResult(tuple(remaining), tuple(skipped), tuple(suppressed))
