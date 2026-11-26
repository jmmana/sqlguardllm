from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from sqlguardllm import Policy
from sqlguardllm.schema import policy_schema

ROOT = Path(__file__).parents[1]
PUBLISHED = ROOT / "schema" / "policy.schema.json"


def test_published_schema_is_up_to_date() -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert published == policy_schema(), "run: sqlguardllm schema > schema/policy.schema.json"


def test_schema_covers_every_policy_field() -> None:
    assert set(policy_schema()["properties"]) == {f.name for f in fields(Policy)}


def test_example_policy_is_valid() -> None:
    data = yaml.safe_load((ROOT / "examples" / "policy.yaml").read_text(encoding="utf-8"))
    jsonschema.validate(data, policy_schema())
    Policy.from_dict(data)


@pytest.mark.parametrize(
    "data",
    [
        {"tables": ["invoices"]},
        {"dialect": "postgres", "tabels": ["invoices"]},
        {"dialect": "postgres", "max_rows": 0},
        {"dialect": "postgres", "row_filters": {"invoices": 1}},
        {"dialect": "postgres", "required_filters": {"invoices": []}},
        {"dialect": "postgres", "function_policy": "maybe"},
    ],
)
def test_invalid_policies_fail_the_schema_and_the_loader(data: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, policy_schema())
    with pytest.raises((ValueError, TypeError)):
        Policy.from_dict(data)
