from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import jsonschema
import pytest
import yaml

from sqlguardllm import Guard
from sqlguardllm.cli import main

ROOT = Path(__file__).parents[1]
POLICY = ROOT / "examples" / "policy.yaml"
ROLES = ROOT / "examples" / "roles.yaml"
DEMO = ROOT / "examples" / "policy-tests.yaml"
PUBLISHED_SCHEMA = ROOT / "schema" / "policy-tests.schema.json"


def cli(*args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def cases_file(tmp_path: Path, *cases: dict[str, Any]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_demo_policy_cases_in_ci() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sqlguardllm", "test", str(DEMO), "--policy", str(POLICY)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS branch manager can read invoices" in result.stdout
    assert result.stdout.endswith("3 passed, 0 failed\n")


def test_cases_schema_command_matches_published_schema() -> None:
    code, output, error = cli("schema", "--cases")
    assert code == 0 and not error
    schema = json.loads(output)
    assert schema == json.loads(PUBLISHED_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(yaml.safe_load(DEMO.read_text(encoding="utf-8")), schema)


def test_cases_schema_flag_conflicts_with_policy_set(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = cli("schema", "--cases", "--policy-set")
    assert code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_json_cases_select_roles_and_supply_context(tmp_path: Path) -> None:
    path = cases_file(
        tmp_path,
        {
            "sql": "SELECT id FROM invoices",
            "role": "manager",
            "expect": "allow",
            "context": {"user": {"branches": [7]}},
            "sql_contains": "branch_id IN (7)",
        },
        {"sql": "SELECT id FROM invoices", "role": "auditor", "expect": "allow"},
        {
            "sql": "SELECT email FROM customers",
            "role": "manager",
            "expect": "reject",
            "violation": "hidden_column",
        },
    )
    code, output, error = cli("test", str(path), "--policy", str(ROLES))
    assert code == 0 and not error
    assert output == "PASS case 1\nPASS case 2\nPASS case 3\n3 passed, 0 failed\n"


@pytest.mark.parametrize("role", [None, "unknown-role"])
def test_roles_are_validated_before_any_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str | None,
) -> None:
    def unexpected_check(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("the guard ran before all roles had been validated")

    monkeypatch.setattr(Guard, "check", unexpected_check)
    first = {"sql": "SELECT 1", "expect": "allow", "role": "manager"}
    second = {"sql": "SELECT 1", "expect": "allow"}
    if role is not None:
        second["role"] = role
    path = cases_file(tmp_path, first, second)
    code, output, error = cli("test", str(path), "--policy", str(ROLES))
    assert code == 2 and not output
    assert "case 2" in error and "role" in error


def test_case_roles_require_a_policy_set(tmp_path: Path) -> None:
    path = cases_file(tmp_path, {"sql": "SELECT 1", "expect": "allow", "role": "manager"})
    code, output, error = cli("test", str(path), "--policy", str(POLICY))
    assert code == 2 and not output
    assert "roles require a policy set" in error


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"cases": []},
        {"cases": {"sql": "SELECT 1"}},
        {"cases": [{"sql": "   ", "expect": "allow"}]},
        {"cases": [{"sql": "SELECT 1"}]},
        {"cases": [{"sql": "SELECT 1", "expect": "unknown"}]},
        {"cases": [{"sql": "SELECT 1", "expect": "allow", "description": "unknown field"}]},
        {"cases": [{"sql": "SELECT 1", "expect": "allow", "context": []}]},
        {"cases": [{"sql": "SELECT 1", "expect": "reject", "violation": "HIDDEN_COLUMN"}]},
        {"cases": [{"sql": "SELECT 1", "expect": "allow", "violation": "hidden_column"}]},
        {"cases": [{"sql": "SELECT 1", "expect": "reject", "sql_contains": "LIMIT 2"}]},
    ],
)
def test_invalid_cases_fail_schema_and_cli(tmp_path: Path, data: Any) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    schema = json.loads(PUBLISHED_SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)
    code, output, error = cli("test", str(path), "--policy", str(POLICY))
    assert code == 2 and not output
    assert "error:" in error


@pytest.mark.parametrize("kind", ["cases", "policy"])
def test_invalid_yaml_is_a_usage_error(tmp_path: Path, kind: str) -> None:
    invalid = tmp_path / "invalid.yaml"
    marker = "private malformed text"
    invalid.write_text(f"broken: [{marker}\n", encoding="utf-8")
    cases = invalid if kind == "cases" else DEMO
    policy = invalid if kind == "policy" else POLICY
    code, output, error = cli("test", str(cases), "--policy", str(policy))
    assert code == 2 and not output
    assert "invalid YAML syntax" in error
    assert marker not in error


def test_missing_cases_file_is_a_usage_error(tmp_path: Path) -> None:
    code, output, error = cli("test", str(tmp_path / "missing.json"), "--policy", str(POLICY))
    assert code == 2 and not output
    assert "cannot read the cases file" in error


def test_reports_failed_expectations_with_exit_1(tmp_path: Path) -> None:
    path = cases_file(
        tmp_path,
        {"sql": "SELECT 1", "expect": "reject"},
        {"sql": "SELECT email FROM customers", "expect": "allow"},
        {"sql": "SELECT email FROM customers", "expect": "reject", "violation": "row_lock"},
        {"sql": "SELECT 1", "expect": "allow", "sql_contains": "select 1"},
    )
    code, output, error = cli("test", str(path), "--policy", str(POLICY))
    assert code == 1 and not error
    assert "FAIL case 1: expected reject, got allow" in output
    assert "FAIL case 2: expected allow, got reject (hidden_column)" in output
    assert "FAIL case 3: expected violation row_lock, got hidden_column" in output
    assert "FAIL case 4: rewritten SQL did not contain the expected substring" in output
    assert output.endswith("0 passed, 4 failed\n")


def test_reject_matches_one_code_among_several(tmp_path: Path) -> None:
    path = cases_file(
        tmp_path,
        {
            "sql": "SELECT email FROM customers JOIN outside_policy"
            " ON customers.id = outside_policy.id",
            "expect": "reject",
            "violation": "hidden_column",
        },
    )
    code, output, error = cli("test", str(path), "--policy", str(POLICY))
    assert code == 0 and not error
    assert "1 passed, 0 failed" in output


def test_junit_escapes_labels_and_omits_sensitive_case_data(tmp_path: Path) -> None:
    label = '<branch & "report">'
    path = cases_file(
        tmp_path,
        {
            "name": label + "\x01",
            "sql": "SELECT '<private & query>'",
            "expect": "allow",
            "context": {"token": "private-context"},
            "sql_contains": "private-substring",
        },
        {"sql": "SELECT 1", "expect": "allow"},
    )
    code, output, error = cli("test", str(path), "--policy", str(POLICY), "--format", "junit")
    assert code == 1 and not error
    suite = ElementTree.fromstring(output)
    assert suite.tag == "testsuite"
    assert {key: suite.get(key) for key in ("tests", "failures")} == {"tests": "2", "failures": "1"}
    first, second = suite.findall("testcase")
    assert first.get("name") == label
    assert first.find("failure") is not None
    assert second.get("name") == "case 2"
    assert second.find("failure") is None
    assert "private" not in output
