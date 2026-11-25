from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import standard_policy
from sqlguardllm import Guard, Policy, PolicyError

EXAMPLE = Path(__file__).parents[1] / "examples" / "policy.yaml"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"dialect": ""}, "dialect"),
        ({"dialect": "postgres", "max_rows": 0}, "max_rows"),
        ({"dialect": "postgres", "max_rows": True}, "max_rows"),
        ({"dialect": "postgres", "tables": "invoices"}, "collection"),
        ({"dialect": "postgres", "function_policy": "allowlist"}, "allowed_functions"),
        ({"dialect": "postgres", "function_policy": "maybe"}, "function_policy"),
    ],
)
def test_invalid_policy_values(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        Policy(**kwargs)  # type: ignore[arg-type]


def test_unknown_dialect() -> None:
    with pytest.raises(PolicyError, match="unknown dialect"):
        Guard(Policy(dialect="not-a-dialect"))


def test_hidden_column_needs_table() -> None:
    with pytest.raises(PolicyError, match=r"table\.column"):
        Guard(Policy(dialect="postgres", hidden_columns={"email"}))


def test_row_filter_for_table_outside_allowlist() -> None:
    with pytest.raises(PolicyError, match="not in the allowed tables"):
        Guard(standard_policy(row_filters={"secrets": "owner = {user.id}"}))


@pytest.mark.parametrize(
    "template",
    [
        "owner = '{user.id}'",  # inside quotes
        "{user.col} (x)",  # as a function
        "branch_id IN (SELECT id FROM branches)",  # subquery
        "branch_id IN (",  # not SQL
    ],
)
def test_bad_row_filters(template: str) -> None:
    with pytest.raises(PolicyError):
        Guard(standard_policy(row_filters={"invoices": template}))


def test_from_dict_rejects_unknown_keys() -> None:
    with pytest.raises(PolicyError, match="unknown policy keys: tabels"):
        Policy.from_dict({"dialect": "postgres", "tabels": ["invoices"]})


def test_from_file_yaml_example() -> None:
    policy = Policy.from_file(EXAMPLE)
    guard = Guard(policy)
    result = guard.check("SELECT id FROM invoices", {"user": {"branches": [3]}})
    assert result.ok
    assert result.sql is not None and "branch_id IN (3)" in result.sql


def test_from_file_json(tmp_path: Path) -> None:
    file = tmp_path / "policy.json"
    file.write_text(json.dumps({"dialect": "sqlite", "tables": ["t"], "max_rows": 5}))
    result = Guard(Policy.from_file(file)).check("SELECT a FROM t")
    assert result.ok and result.sql == "SELECT a FROM t LIMIT 5"
