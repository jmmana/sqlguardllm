from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from sqlguardllm import Guard, Policy, PolicyError, PolicySet, ViolationCode
from sqlguardllm.cli import main
from sqlguardllm.schema import policy_set_schema

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "roles.yaml"
PUBLISHED = ROOT / "schema" / "policy-set.schema.json"
CONTEXT = {"user": {"branches": [1, 2]}}

BASE: dict[str, Any] = {
    "dialect": "postgres",
    "tables": ["invoices", "branches"],
    "hidden_columns": ["invoices.notes"],
    "row_filters": {"invoices": "branch_id IN ({user.branches})"},
    "max_rows": 1000,
}


def policy_set(**roles: Any) -> PolicySet:
    return PolicySet.from_dict({"base": BASE, "roles": roles})


def test_example_file() -> None:
    roles = PolicySet.from_file(EXAMPLE)
    assert roles.roles == ("analyst", "auditor", "manager")
    manager, analyst, auditor = (roles.for_role(r) for r in ("manager", "analyst", "auditor"))
    assert "customers" in set(manager.tables or ())
    assert {"customers.email", "invoices.internal_notes"} <= set(manager.hidden_columns)
    assert analyst.max_rows == 200
    assert set(analyst.hidden_columns) == {*manager.hidden_columns, "customers.name"}
    assert dict(analyst.required_filters) == {"invoices": {"issued_on"}}
    assert dict(analyst.row_filters) == dict(roles.base.row_filters)
    assert dict(auditor.row_filters) == {}
    assert auditor.max_joins == 3


def test_example_guards_follow_each_role() -> None:
    roles = PolicySet.from_file(EXAMPLE)
    sql = "SELECT c.name, SUM(i.total) FROM customers c JOIN invoices i ON i.customer_id = c.id"
    assert roles.guard("manager").check(sql, CONTEXT).ok
    assert roles.guard("analyst").check(sql, CONTEXT).codes == {
        ViolationCode.HIDDEN_COLUMN,
        ViolationCode.MISSING_REQUIRED_FILTER,
    }
    audited = roles.guard("auditor").check(sql)
    assert audited.ok and audited.applied_filters == ()


def test_a_role_without_changes_is_the_base() -> None:
    roles = policy_set(viewer=None, reader={})
    assert roles.for_role("viewer") == roles.base == Policy.from_dict(BASE)
    assert roles.for_role("reader") == roles.base


def test_scalars_override() -> None:
    roles = policy_set(
        small={"max_rows": 10, "forbid_cartesian": True}, unlimited={"max_rows": None}
    )
    assert roles.for_role("small").max_rows == 10
    assert roles.for_role("small").forbid_cartesian is True
    assert roles.for_role("unlimited").max_rows is None
    assert roles.base.max_rows == 1000


def test_lists_and_mappings_set_in_a_role_replace_the_inherited_value() -> None:
    roles = policy_set(
        narrow={"tables": ["branches"], "hidden_columns": [], "row_filters": {}},
        other_filter={"row_filters": {"branches": "id IN ({user.branches})"}},
    )
    narrow = roles.for_role("narrow")
    assert narrow.tables == {"branches"}
    assert narrow.hidden_columns == frozenset()
    assert dict(narrow.row_filters) == {}
    assert dict(roles.for_role("other_filter").row_filters) == {
        "branches": "id IN ({user.branches})"
    }


def test_add_extends_lists_and_merges_mappings() -> None:
    roles = policy_set(
        wider={
            "add": {
                "tables": ["products"],
                "hidden_columns": ["products.cost"],
                "row_filters": {
                    "branches": "id IN ({user.branches})",
                    "invoices": "branch_id = {user.main_branch}",
                },
                "required_filters": {"invoices": ["issued_on"]},
                "extra_denied_functions": ["refresh_cache"],
            }
        }
    )
    wider = roles.for_role("wider")
    assert wider.tables == {"invoices", "branches", "products"}
    assert wider.hidden_columns == {"invoices.notes", "products.cost"}
    assert dict(wider.row_filters) == {
        "invoices": "branch_id = {user.main_branch}",  # the role wins for a key in both
        "branches": "id IN ({user.branches})",
    }
    assert dict(wider.required_filters) == {"invoices": {"issued_on"}}
    assert wider.extra_denied_functions == {"refresh_cache"}
    assert roles.base.tables == {"invoices", "branches"}  # the base is not modified


def test_add_works_for_every_mapping_key() -> None:
    careful = policy_set(
        careful={
            "add": {
                "masked_columns": {"invoices.customer": "hash"},
                "aggregate_only": {"invoices": 5},
            }
        }
    ).for_role("careful")
    assert dict(careful.masked_columns) == {"invoices.customer": "hash"}
    assert dict(careful.aggregate_only) == {"invoices": 5}
    with pytest.raises(PolicyError, match="role 'clash': a column can't be both hidden and masked"):
        policy_set(clash={"add": {"masked_columns": {"invoices.notes": "null"}}})
    with pytest.raises(PolicyError, match="can't add to schema"):
        policy_set(wide={"add": {"schema": {"invoices": ["id"]}}})


def test_extends_inherits_from_another_role() -> None:
    roles = policy_set(
        senior={"extends": "manager", "max_rows": 50},
        manager={"add": {"tables": ["products"]}, "max_joins": 2},
        junior={"extends": "senior", "add": {"hidden_columns": ["products.cost"]}},
    )
    junior = roles.for_role("junior")
    assert junior.tables == {"invoices", "branches", "products"}
    assert junior.max_joins == 2
    assert junior.max_rows == 50
    assert junior.hidden_columns == {"invoices.notes", "products.cost"}
    assert roles.for_role("manager").max_rows == 1000


@pytest.mark.parametrize(
    ("roles", "message"),
    [
        ({"a": {"extends": "a"}}, r"cycle: a -> a"),
        ({"a": {"extends": "b"}, "b": {"extends": "a"}}, r"cycle: a -> b -> a"),
        (
            {"a": {"extends": "b"}, "b": {"extends": "c"}, "c": {"extends": "b"}},
            r"cycle: b -> c -> b",
        ),
        ({"a": {"extends": "nobody"}}, "extends unknown role 'nobody'"),
        ({"a": {"extends": 3}}, "extends unknown role 3"),
        ({"a": {"tabels": ["x"]}}, "unknown keys in role 'a': tabels"),
        ({"a": {"add": {"tabels": ["x"]}}}, "unknown key in add of role 'a': tabels"),
        ({"a": {"add": ["tables"]}}, "add in role 'a' must map"),
        ({"a": {"add": {"max_rows": 5}}}, "can only add to lists and mappings, not max_rows"),
        ({"a": {"add": {"denied_functions": ["f"]}}}, "inherited value is null"),
        ({"a": {"tables": ["x"], "add": {"tables": ["y"]}}}, "both sets and adds tables"),
        ({"a": {"add": {"tables": "products"}}}, r"add\.tables in role 'a' must be a list"),
        ({"a": {"add": {"tables": [["x"]]}}}, r"add\.tables in role 'a' must contain strings"),
        ({"a": {"add": {"row_filters": ["x"]}}}, r"add\.row_filters in role 'a' must be a map"),
        ({"a": {"max_rows": 0}}, "role 'a': max_rows"),
        ({"a": {"add": {"tables": [""]}}}, "role 'a': tables must contain non-empty strings"),
        ({"a": ["tables"]}, "role 'a' must be a mapping"),
        ({"": {}}, "role names must be non-empty strings"),
    ],
)
def test_invalid_roles(roles: dict[str, Any], message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        policy_set(**roles) if "" not in roles else PolicySet(Policy.from_dict(BASE), roles)


def test_roles_are_compiled_when_the_set_is_loaded() -> None:
    # A row filter for a table the role no longer allows only fails when compiled.
    with pytest.raises(PolicyError, match="role 'narrow': row filter for 'invoices'"):
        policy_set(narrow={"tables": ["branches"]})
    with pytest.raises(PolicyError, match="role 'odd': unknown dialect"):
        policy_set(odd={"dialect": "not-a-dialect"})


def test_unknown_role() -> None:
    roles = policy_set(manager={})
    with pytest.raises(PolicyError, match="unknown role 'boss'"):
        roles.for_role("boss")
    with pytest.raises(PolicyError, match="unknown role 'boss'"):
        roles.guard("boss")
    with pytest.raises(PolicyError, match="unknown role"):
        roles.for_role(None)  # type: ignore[arg-type]


def test_guards_are_cached_per_role_language_and_pretty() -> None:
    roles = policy_set(manager={}, analyst={"max_rows": 10})
    guard = roles.guard("manager")
    assert isinstance(guard, Guard)
    assert roles.guard("manager") is guard
    assert roles.guard("manager", language="es") is not guard
    assert roles.guard("manager", language="es") is roles.guard("manager", language="es")
    assert roles.guard("manager", pretty=True) is not guard
    assert roles.guard("analyst") is not guard
    assert roles.guard("analyst").policy == roles.for_role("analyst")
    assert roles.guard("manager", language="es").check("SELECT x FROM secrets").messages == [
        "secrets no es una tabla permitida"
    ]
    with pytest.raises(PolicyError, match="language"):
        roles.guard("manager", language="fr")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"roles": {}}, "needs a base policy"),
        ({"base": BASE, "role": {}}, "unknown policy set keys: role"),
        ({"base": BASE, "roles": ["manager"]}, "roles must map"),
        ({"base": {"tables": ["x"]}}, "needs a dialect"),
        (["base"], "must be a mapping"),
    ],
)
def test_invalid_files(data: Any, message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        PolicySet.from_dict(data)


def test_constructor() -> None:
    roles = PolicySet(Policy(dialect="sqlite", tables={"t"}), {"r": {"max_rows": 5}})
    assert roles.for_role("r").max_rows == 5
    assert PolicySet(Policy(dialect="sqlite")).roles == ()
    with pytest.raises(PolicyError, match="base must be a Policy"):
        PolicySet(BASE)  # type: ignore[arg-type]


def test_from_json_file(tmp_path: Path) -> None:
    path = tmp_path / "roles.json"
    path.write_text(json.dumps({"base": BASE, "roles": {"r": {"max_rows": 5}}}), encoding="utf-8")
    assert PolicySet.from_file(path).for_role("r").max_rows == 5
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(PolicyError, match="mapping at the top level"):
        PolicySet.from_file(path)


# JSON Schema of policy-set files


def test_published_schema_is_up_to_date() -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert published == policy_set_schema(), (
        "run: sqlguardllm schema --policy-set > schema/policy-set.schema.json"
    )


def test_cli_prints_the_policy_set_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "--policy-set"]) == 0
    assert json.loads(capsys.readouterr().out) == policy_set_schema()


def test_example_is_valid_for_the_schema() -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    jsonschema.validate(data, policy_set_schema())


def test_schema_accepts_what_the_loader_accepts() -> None:
    data = {
        "base": BASE,
        "roles": {
            "viewer": None,
            "manager": {"add": {"tables": ["products"], "row_filters": {"branches": "id = 1"}}},
            "analyst": {
                "extends": "manager",
                "max_rows": None,
                "tables": ["branches"],
                "row_filters": {},
            },
        },
    }
    jsonschema.validate(data, policy_set_schema())
    PolicySet.from_dict(data)


@pytest.mark.parametrize(
    "data",
    [
        {"roles": {}},
        {"base": BASE, "extra": 1},
        {"base": {**BASE, "tabels": []}},
        {"base": BASE, "roles": {"a": {"tabels": ["x"]}}},
        {"base": BASE, "roles": {"a": {"add": {"max_rows": 5}}}},
        {"base": BASE, "roles": {"a": {"add": {"tables": None}}}},
        {"base": BASE, "roles": {"a": {"max_rows": 0}}},
        {"base": BASE, "roles": {"a": {"extends": ""}}},
        {"base": BASE, "roles": {"a": ["tables"]}},
    ],
)
def test_invalid_sets_fail_the_schema_and_the_loader(data: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, policy_set_schema())
    with pytest.raises(PolicyError):
        PolicySet.from_dict(data)
