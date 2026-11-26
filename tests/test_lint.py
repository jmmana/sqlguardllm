from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sqlguardllm.lint import lint_policy
from sqlguardllm.policy import Policy
from sqlguardllm.result import PolicyError

ROOT = Path(__file__).parents[1]


def codes(policy: Policy) -> set[str]:
    return {warning.code for warning in lint_policy(policy).warnings}


def test_tenant_column_without_row_filter_warns() -> None:
    policy = Policy(dialect="postgres", schema={"customers": ["id", "tenant_id"]})
    assert "tenant_filter_missing" in codes(policy)


def test_tenant_column_with_row_filter_is_safe() -> None:
    policy = Policy(
        dialect="postgres",
        schema={"customers": ["tenant_id"]},
        row_filters={"customers": "tenant_id = {user.tenant_id}"},
    )
    assert "tenant_filter_missing" not in codes(policy)


def test_tenant_filter_on_another_schema_does_not_count() -> None:
    policy = Policy(
        dialect="postgres",
        schema={"sales.customers": ["tenant_id"]},
        row_filters={"archive.customers": "tenant_id = 1"},
    )
    assert "tenant_filter_missing" in codes(policy)


def test_sensitive_columns_need_hidden_or_masked() -> None:
    policy = Policy(
        dialect="postgres",
        schema={"customers": ["email", "card_number", "name"]},
        hidden_columns={"customers.email"},
        masked_columns={"customers.card_number": "redact"},
    )
    assert "sensitive_column_unprotected" not in codes(policy)


def test_sensitive_column_warning() -> None:
    policy = Policy(dialect="postgres", schema={"customers": ["email"]})
    assert "sensitive_column_unprotected" in codes(policy)


def test_max_rows_unset_or_above_threshold_warns() -> None:
    assert "max_rows_weak" in codes(Policy(dialect="postgres", max_rows=None))
    assert "max_rows_weak" in codes(Policy(dialect="postgres", max_rows=10_001))
    assert "max_rows_weak" not in codes(Policy(dialect="postgres", max_rows=10_000))


def test_low_aggregate_k_warns() -> None:
    assert "aggregate_k_low" in codes(Policy(dialect="postgres", aggregate_only={"users": 4}))
    assert "aggregate_k_low" not in codes(Policy(dialect="postgres", aggregate_only={"users": 5}))


def test_reallowing_default_denied_function_warns() -> None:
    assert "default_denylist_reduced" in codes(
        Policy(dialect="postgres", denied_functions=["sleep"])
    )
    assert "default_denylist_reduced" in codes(
        Policy(dialect="postgres", function_policy="allowlist", allowed_functions=["pg_sleep"])
    )


def test_suppressions_require_reason_and_hide_matching_warning() -> None:
    policy = Policy(
        dialect="postgres",
        max_rows=None,
        lint_suppressions={"max_rows_weak": "needed for the export job"},
    )
    result = lint_policy(policy)
    assert "max_rows_weak" not in {warning.code for warning in result.warnings}
    assert result.suppressed[0].reason == "needed for the export job"


def test_suppression_without_a_matching_warning_is_reported() -> None:
    policy = Policy(dialect="postgres", lint_suppressions={"unknown_warning": "accepted"})
    assert "unknown_suppression" in codes(policy)


def test_suppression_reason_must_not_be_empty() -> None:
    with pytest.raises(PolicyError, match="non-empty reasons"):
        Policy(dialect="postgres", lint_suppressions={"max_rows_weak": " "})


def test_missing_schema_reports_skipped_column_checks() -> None:
    result = lint_policy(Policy(dialect="postgres"))
    assert {item.code for item in result.skipped} == {
        "tenant_filter_missing",
        "sensitive_column_unprotected",
    }


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sqlguardllm", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_lint_json_and_strict_exit_code(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"dialect": "postgres", "max_rows": None}))
    result = run("lint", "--policy", str(policy), "--format", "json", "--strict")
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["warnings"][0]["code"] == "max_rows_weak"
    assert data["warnings"][0]["fix"]
    assert {item["code"] for item in data["skipped"]} == {
        "tenant_filter_missing",
        "sensitive_column_unprotected",
    }


def test_lint_warns_without_strict_and_demo_policies_are_clean() -> None:
    policy = ROOT / "examples" / "policy.yaml"
    result = run("lint", "--policy", str(policy), "--strict")
    assert result.returncode == 0
    assert "skipped[tenant_filter_missing]" in result.stdout

    roles = ROOT / "examples" / "roles.yaml"
    mcp_roles = ROOT / "examples" / "mcp" / "roles.yaml"
    for path, role in (
        (roles, "manager"),
        (roles, "analyst"),
        (roles, "auditor"),
        (mcp_roles, "manager"),
        (mcp_roles, "auditor"),
    ):
        result = run("lint", "--policy", str(path), "--role", role, "--format", "json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["warnings"] == []
