"""Run declared SQL expectations against a policy without executing any SQL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from sqlguardllm.guard import Guard
from sqlguardllm.policy import Policy, read_mapping
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import CheckResult, PolicyError, ViolationCode

_CASE_FIELDS = {"name", "sql", "role", "context", "expect", "violation", "sql_contains"}


class PolicyTestError(Exception):
    """Invalid input to a policy test run."""


@dataclass(frozen=True)
class _Case:
    name: str
    sql: str
    expect: str
    role: str | None
    context: Mapping[str, Any] | None
    violation: ViolationCode | None
    sql_contains: str | None


@dataclass(frozen=True)
class Outcome:
    name: str
    failure: str | None


def _load(path: Path, kind: str) -> Mapping[str, Any]:
    try:
        return read_mapping(path)
    except OSError as exc:
        raise PolicyTestError(f"cannot read the {kind} file") from exc
    except PolicyError as exc:
        raise PolicyTestError(f"invalid {kind} file: {exc}") from exc
    except ValueError as exc:
        raise PolicyTestError(f"invalid {kind} file") from exc


def _case(value: object, index: int) -> _Case:
    label = f"case {index}"
    if not isinstance(value, dict) or value.keys() - _CASE_FIELDS:
        raise PolicyTestError(f"{label} must be an object with known fields")
    sql = value.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise PolicyTestError(f"{label} needs a non-blank sql string")
    expect = value.get("expect")
    if expect not in ("allow", "reject"):
        raise PolicyTestError(f"{label} must expect allow or reject")
    for field in ("name", "role"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise PolicyTestError(f"{label} {field} must be a non-blank string")
    context = value.get("context")
    if "context" in value and not isinstance(context, dict):
        raise PolicyTestError(f"{label} context must be an object")
    violation = None
    if "violation" in value:
        if expect != "reject":
            raise PolicyTestError(f"{label} violation requires expect: reject")
        try:
            violation = ViolationCode(value["violation"])
        except ValueError as exc:
            raise PolicyTestError(f"{label} violation must be a public violation code") from exc
    sql_contains = value.get("sql_contains")
    if "sql_contains" in value:
        if expect != "allow":
            raise PolicyTestError(f"{label} sql_contains requires expect: allow")
        if not isinstance(sql_contains, str) or not sql_contains:
            raise PolicyTestError(f"{label} sql_contains must be a non-empty string")
    # Keep user-provided labels on one line and valid in XML, including when read from JSON.
    name = "".join(char if char.isprintable() else " " for char in value.get("name", label))
    return _Case(
        name.strip() or label, sql, expect, value.get("role"), context, violation, sql_contains
    )


def _guards(policy_file: Path, cases: list[_Case]) -> list[Guard]:
    data = _load(policy_file, "policy")
    try:
        if "base" not in data and "roles" not in data:
            if any(case.role is not None for case in cases):
                raise PolicyTestError("case roles require a policy set")
            return [Guard(Policy.from_dict(data))] * len(cases)
        policies = PolicySet.from_dict(data)
        roles = set(policies.roles)
        guards = []
        for index, case in enumerate(cases, 1):
            if case.role is None:
                raise PolicyTestError(f"case {index} needs a role for the policy set")
            if case.role not in roles:
                raise PolicyTestError(f"case {index} names an unknown policy-set role")
            guards.append(policies.guard(case.role))
        return guards
    except (TypeError, ValueError) as exc:
        raise PolicyTestError("invalid policy or policy set") from exc


def _failure(case: _Case, result: CheckResult) -> str | None:
    actual = "allow" if result.ok else "reject"
    codes = ", ".join(sorted(code.value for code in result.codes)) or "none"
    if case.expect != actual:
        detail = f" ({codes})" if not result.ok else ""
        return f"expected {case.expect}, got {actual}{detail}"
    if case.violation is not None and case.violation not in result.codes:
        return f"expected violation {case.violation.value}, got {codes}"
    if case.sql_contains is not None and case.sql_contains not in (result.sql or ""):
        return "rewritten SQL did not contain the expected substring"
    return None


def run_policy_tests(cases_file: Path, policy_file: Path) -> list[Outcome]:
    """Validate the full test file and role selections before checking any cases."""
    data = _load(cases_file, "cases")
    values = data.get("cases")
    if set(data) != {"cases"} or not isinstance(values, list) or not values:
        raise PolicyTestError("cases file must contain a non-empty cases list")
    cases = [_case(value, index) for index, value in enumerate(values, 1)]
    guards = _guards(policy_file, cases)
    return [
        Outcome(case.name, _failure(case, guard.check(case.sql, case.context)))
        for case, guard in zip(cases, guards, strict=True)
    ]


def format_outcomes(outcomes: list[Outcome], *, junit: bool) -> str:
    failed = sum(outcome.failure is not None for outcome in outcomes)
    if junit:
        suite = ElementTree.Element(
            "testsuite",
            name="sqlguardllm",
            tests=str(len(outcomes)),
            failures=str(failed),
            errors="0",
            skipped="0",
        )
        for outcome in outcomes:
            case = ElementTree.SubElement(
                suite, "testcase", classname="sqlguardllm", name=outcome.name
            )
            if outcome.failure is not None:
                ElementTree.SubElement(case, "failure", message=outcome.failure)
        return ElementTree.tostring(suite, encoding="unicode")

    lines = [
        f"PASS {outcome.name}"
        if outcome.failure is None
        else f"FAIL {outcome.name}: {outcome.failure}"
        for outcome in outcomes
    ]
    lines.append(f"{len(outcomes) - failed} passed, {failed} failed")
    return "\n".join(lines)
