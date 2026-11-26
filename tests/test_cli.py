from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
POLICY = ROOT / "examples" / "policy.yaml"
CONTEXT = '{"user": {"branches": [1, 2]}}'


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sqlguardllm", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_allowed_prints_the_rewritten_sql() -> None:
    result = run("check", "--policy", str(POLICY), "--context", CONTEXT, "SELECT id FROM invoices")
    assert result.returncode == 0, result.stderr
    assert "branch_id IN (1, 2)" in result.stdout
    assert "LIMIT 1000" in result.stdout


def test_reads_sql_from_stdin() -> None:
    result = run("check", "--policy", str(POLICY), "--context", CONTEXT, stdin="SELECT 1 AS x\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SELECT 1 AS x LIMIT 1000"


def test_rejected_prints_violations_with_exit_1() -> None:
    result = run("check", "--policy", str(POLICY), "--language", "es", "DELETE FROM invoices")
    assert result.returncode == 1
    assert result.stdout.startswith("not_a_query: Solo se permiten consultas SELECT")


def test_json_format() -> None:
    result = run(
        "check", "--policy", str(POLICY), "--format", "json", "SELECT email FROM customers"
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["sql"] is None
    assert [v["code"] for v in data["violations"]] == ["hidden_column"]


def test_missing_context_is_a_violation_not_a_usage_error() -> None:
    result = run("check", "--policy", str(POLICY), "SELECT id FROM invoices")
    assert result.returncode == 1
    assert result.stdout.startswith("missing_context:")


def test_dialect_override() -> None:
    result = run(
        "check",
        "--policy",
        str(POLICY),
        "--dialect",
        "tsql",
        "--context",
        CONTEXT,
        "SELECT TOP 5 id FROM invoices",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("SELECT TOP 5 id")


def test_context_from_file(tmp_path: Path) -> None:
    context = tmp_path / "context.json"
    context.write_text(CONTEXT)
    result = run("check", "--policy", str(POLICY), "--context", f"@{context}", "SELECT 1 AS x")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["check", "--policy", "does-not-exist.yaml", "SELECT 1"], "cannot read the policy"),
        (["check", "--policy", str(POLICY), "--context", "{nope", "SELECT 1"], "not valid JSON"),
        (["check", "--policy", str(POLICY), "--context", "[1]", "SELECT 1"], "JSON object"),
        (["check", "--policy", str(POLICY), "   "], "no SQL given"),
        (["check", "--policy", str(POLICY), "--dialect", "klingon", "SELECT 1"], "dialect"),
        (["check", "SELECT 1"], "--policy"),
        ([], "command"),
    ],
)
def test_usage_and_policy_errors_exit_2(args: list[str], message: str) -> None:
    result = run(*args)
    assert result.returncode == 2
    assert message in result.stderr


def test_invalid_policy_file_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"dialect": "postgres", "tabels": ["invoices"]}))
    result = run("check", "--policy", str(bad), "SELECT 1")
    assert result.returncode == 2
    assert "tabels" in result.stderr


def test_console_script_is_declared() -> None:
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("sqlguardllm") == "sqlguardllm.cli:main"


def test_version() -> None:
    result = run("--version")
    assert result.returncode == 0
    assert result.stdout.startswith("sqlguardllm ")
