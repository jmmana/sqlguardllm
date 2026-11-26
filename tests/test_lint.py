from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from sqlguardllm import Guard, Policy, PolicyError, PolicySet
from sqlguardllm.cli import main
from sqlguardllm.data.denied_functions import default_denylist
from sqlguardllm.lint import (
    _TEXTS,
    LintCode,
    LintReport,
    format_report,
    lint_policy,
    lint_policy_set,
    looks_like_tenant,
    looks_sensitive,
)
from sqlguardllm.schema import policy_schema, policy_set_schema

ROOT = Path(__file__).parents[1]
DEMO_POLICIES = [
    ROOT / "examples" / "policy.yaml",
    ROOT / "examples" / "roles.yaml",
    ROOT / "examples" / "mcp" / "roles.yaml",
]
SCHEMA = {
    "orders": ["id", "tenant_id", "total"],
    "customers": ["id", "tenant_id", "name", "email"],
}


def cli(*args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def safe(**changes: Any) -> dict[str, Any]:
    """A policy that lints clean; each test weakens one thing."""
    data: dict[str, Any] = {
        "dialect": "postgres",
        "tables": ["orders", "customers"],
        "row_filters": {
            "orders": "tenant_id = {user.tenant}",
            "customers": "tenant_id = {user.tenant}",
        },
        "hidden_columns": ["customers.email"],
        "schema": SCHEMA,
    }
    data.update(changes)
    return data


def codes(report: LintReport) -> list[str]:
    return [item.code.value for item in report.warnings]


def lint(data: dict[str, Any], **options: Any) -> LintReport:
    return lint_policy(Policy.from_dict(data), **options)


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_codes_are_stable() -> None:
    assert [code.value for code in LintCode] == [
        "no-table-allowlist",
        "tenant-without-row-filter",
        "sensitive-column-exposed",
        "max-rows-disabled",
        "max-rows-high",
        "aggregate-k-low",
        "denied-function-allowed",
    ]


@pytest.mark.parametrize("code", list(LintCode))
def test_every_code_explains_and_links_a_policy_field_in_both_languages(code: LintCode) -> None:
    field, texts = _TEXTS[code]
    assert field in policy_schema()["properties"]
    assert set(texts) == {"en", "es"}
    for message, explanation, fix in texts.values():
        assert message and explanation and fix


def test_safe_policy_lints_clean() -> None:
    report = lint(safe())
    assert report.warnings == report.suppressed == report.skipped == ()


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"row_filters": {"customers": "tenant_id = {user.tenant}"}}, "tenant-without-row-filter"),
        ({"hidden_columns": []}, "sensitive-column-exposed"),
        ({"max_rows": None}, "max-rows-disabled"),
        ({"max_rows": 50_000}, "max-rows-high"),
        ({"aggregate_only": {"orders": 3}}, "aggregate-k-low"),
        ({"denied_functions": ["pg_sleep"]}, "denied-function-allowed"),
        (
            {"function_policy": "allowlist", "allowed_functions": ["count", "pg_sleep"]},
            "denied-function-allowed",
        ),
        ({"tables": None, "row_filters": {}, "schema": None}, "no-table-allowlist"),
    ],
)
def test_each_code_fires_on_a_weak_policy(changes: dict[str, Any], expected: str) -> None:
    assert codes(lint(safe(**changes))) == [expected]


def test_tenant_warning_names_the_table_and_shows_a_filter() -> None:
    (warning,) = lint(safe(row_filters={"customers": "tenant_id = 1"})).warnings
    assert warning.message == "orders has the tenant-like column tenant_id and no row filter"
    assert 'row_filters: {orders: "tenant_id = {user.tenant_id}"}' in warning.fix
    assert warning.reference.endswith("/policy-reference/#row_filters")


def test_masked_columns_count_as_protected() -> None:
    report = lint(safe(hidden_columns=[], masked_columns={"customers.email": "partial"}))
    assert codes(report) == []


def test_threshold_and_group_size_limits() -> None:
    assert codes(lint(safe(max_rows=10_000))) == []
    assert codes(lint(safe(max_rows=50_000), max_rows_threshold=100_000)) == []
    assert codes(lint(safe(aggregate_only={"orders": 5}))) == []
    (warning,) = lint(safe(max_rows=50_000), max_rows_threshold=20_000).warnings
    assert warning.message == "max_rows is 50000, above the threshold of 20000"


def test_adding_to_the_denylist_is_fine_and_replacing_it_lists_what_is_lost() -> None:
    default = default_denylist("postgres")
    assert codes(lint(safe(extra_denied_functions=["refresh_cache"]))) == []
    assert codes(lint(safe(denied_functions=sorted(default)))) == []
    kept = sorted(default - {"pg_read_file", "pg_sleep"})
    (warning,) = lint(safe(denied_functions=kept)).warnings
    assert warning.message == (
        "denied_functions lets queries call functions that the default denylist of postgres "
        "blocks: pg_read_file, pg_sleep"
    )
    assert warning.reference.endswith("#denied_functions")
    (shortened,) = lint(safe(denied_functions=["pg_sleep"])).warnings
    assert shortened.message.endswith(f" and {len(default) - 1 - 8} more")
    (allowlist,) = lint(
        safe(function_policy="allowlist", allowed_functions=["count", "pg_sleep"])
    ).warnings
    assert "pg_sleep" in allowlist.message and "count" not in allowlist.message
    assert allowlist.reference.endswith("#allowed_functions")


def test_column_checks_are_skipped_without_schema() -> None:
    report = lint(safe(schema=None, hidden_columns=[]))
    assert report.warnings == ()
    assert [item.code.value for item in report.skipped] == [
        "tenant-without-row-filter",
        "sensitive-column-exposed",
    ]
    assert all("schema" in item.reason for item in report.skipped)


def test_tables_missing_from_schema_are_reported_as_skipped() -> None:
    report = lint(safe(tables=["orders", "customers", "products"]))
    assert codes(report) == []
    assert {item.reason for item in report.skipped} == {
        "columns not checked for allowed tables missing from schema: products"
    }


def test_tables_outside_the_allowlist_are_not_checked() -> None:
    schema = {**SCHEMA, "users": ["id", "tenant_id", "password_hash"]}
    assert codes(lint(safe(schema=schema))) == []
    assert codes(lint(safe(tables=None, schema=schema))) == [
        "no-table-allowlist",
        "tenant-without-row-filter",
        "sensitive-column-exposed",
    ]


def test_qualified_names_follow_the_default_schema() -> None:
    report = lint(
        safe(
            default_schema="sales",
            tables=["sales.orders", "customers"],
            row_filters={"orders": "tenant_id = 1", "sales.customers": "tenant_id = 1"},
            hidden_columns=["sales.customers.email"],
            schema={"orders": ["tenant_id"], "sales.customers": ["tenant_id", "email"]},
        )
    )
    assert codes(report) == []


def test_suppression_keeps_the_reason_and_hides_the_warning() -> None:
    reason = "Exports run as a reviewed job with their own limits."
    report = lint(safe(max_rows=None, lint_suppress={"max-rows-disabled": reason}))
    assert report.warnings == ()
    (item,) = report.suppressed
    assert (item.code, item.reason) == (LintCode.MAX_ROWS_DISABLED, reason)
    text = format_report(report)
    assert "SUPPRESSED max-rows-disabled: max_rows is null" in text
    assert f"  Reason: {reason}" in text


def test_suppressing_a_column_check_also_silences_its_skip() -> None:
    report = lint(
        safe(schema=None, lint_suppress={"sensitive-column-exposed": "No personal data here."})
    )
    assert [item.code.value for item in report.skipped] == ["tenant-without-row-filter"]


def test_suppression_doesnt_change_what_the_guard_allows() -> None:
    policy = Policy.from_dict(safe(lint_suppress={"max-rows-high": "Reviewed."}))
    result = Guard(policy).check("SELECT id FROM orders", {"user": {"tenant": 1}})
    assert result.ok and result.sql is not None
    assert "tenant_id = 1" in result.sql and result.sql.endswith("LIMIT 1000")


@pytest.mark.parametrize(
    "suppress",
    [
        {"max-rows-unset": "typo in the code"},
        {"max-rows-disabled": "   "},
        {"max-rows-disabled": 5},
        ["max-rows-disabled"],
    ],
)
def test_invalid_suppressions_fail_the_schema_and_the_loader(suppress: Any) -> None:
    data = safe(lint_suppress=suppress)
    data.pop("schema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, policy_schema())
    with pytest.raises(PolicyError):
        Policy.from_dict(data)


def test_valid_suppressions_pass_the_schemas() -> None:
    suppress = {"max-rows-high": "Reports need large pages."}
    jsonschema.validate(safe(lint_suppress=suppress), policy_schema())
    policy_set = {
        "base": safe(lint_suppress=suppress),
        "roles": {"auditor": {"add": {"lint_suppress": {"no-table-allowlist": "Reviewed."}}}},
    }
    jsonschema.validate(policy_set, policy_set_schema())


def test_policy_sets_report_each_warning_once_with_its_roles() -> None:
    policy_set = PolicySet.from_dict(
        {
            "base": safe(max_rows=50_000),
            "roles": {
                "manager": {},
                "analyst": {"extends": "manager"},
                "auditor": {"row_filters": {}},
            },
        }
    )
    report = lint_policy_set(policy_set)
    assert [(item.code.value, item.roles) for item in report.warnings] == [
        ("tenant-without-row-filter", ("auditor",)),
        ("tenant-without-row-filter", ("auditor",)),
        ("max-rows-high", ("analyst", "auditor", "manager")),
    ]
    text = format_report(report)
    assert "WARN max-rows-high (roles analyst, auditor, manager): max_rows is 50000" in text
    assert "WARN tenant-without-row-filter (role auditor): orders has" in text


def test_policy_set_suppressions_are_inherited_and_added_per_role() -> None:
    policy_set = PolicySet.from_dict(
        {
            "base": safe(max_rows=None, lint_suppress={"max-rows-disabled": "Batch exports."}),
            "roles": {
                "manager": {},
                "auditor": {
                    "row_filters": {},
                    "add": {
                        "lint_suppress": {
                            "tenant-without-row-filter": "Auditors review every tenant.",
                        }
                    },
                },
            },
        }
    )
    report = lint_policy_set(policy_set)
    assert report.warnings == ()
    assert [(item.code.value, item.roles, item.reason) for item in report.suppressed] == [
        ("tenant-without-row-filter", ("auditor",), "Auditors review every tenant."),
        ("tenant-without-row-filter", ("auditor",), "Auditors review every tenant."),
        ("max-rows-disabled", ("auditor", "manager"), "Batch exports."),
    ]


def test_a_policy_set_without_roles_lints_its_base() -> None:
    report = lint_policy_set(PolicySet.from_dict({"base": safe(max_rows=None)}))
    assert [(item.code.value, item.roles) for item in report.warnings] == [
        ("max-rows-disabled", ("base",))
    ]


@pytest.mark.parametrize(
    "name",
    [
        "tenant_id",
        "tenantId",
        "TenantID",
        "company_id",
        "branch_id",
        "org_id",
        "orgid",
        "organization_id",
        "workspace_id",
        "sucursal",
        "id_sucursal",
        "empresa_id",
        "codigo_sucursal",
    ],
)
def test_tenant_like_columns(name: str) -> None:
    assert looks_like_tenant(name)


@pytest.mark.parametrize(
    "name", ["id", "customer_id", "organization_name", "parent_org_id", "branches", "total"]
)
def test_not_tenant_like_columns(name: str) -> None:
    assert not looks_like_tenant(name)


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "password_hash",
        "userPassword",
        "passwordhash",
        "api_key",
        "apiKey",
        "access_token",
        "client_secret",
        "ssn",
        "cedula",
        "tax_id",
        "email",
        "email_address",
        "correo",
        "phone",
        "phone_number",
        "telefono",
        "card",
        "credit_card_number",
        "card_number",
        "iban",
        "contraseña",
        "mobile",
        "card_last4",
        "secret_question",
    ],
)
def test_sensitive_columns(name: str) -> None:
    assert looks_sensitive(name)


@pytest.mark.parametrize(
    "name",
    [
        "id",
        "name",
        "total",
        "cardinality",
        "passenger_count",
        "syntax_id",
        "emailed_at",
        "secretary",
        "token_count",
        "tokens_used",
        "email_verified",
        "password_changed_at",
        "card_type",
        "mobile_app_version",
    ],
)
def test_not_sensitive_columns(name: str) -> None:
    assert not looks_sensitive(name)


@pytest.mark.parametrize("path", DEMO_POLICIES, ids=lambda path: path.parent.name + "/" + path.name)
def test_demo_policies_lint_clean_in_ci(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sqlguardllm", "lint", "--policy", str(path), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN" not in result.stdout
    assert result.stdout.endswith("0 warnings, 0 suppressed, 2 checks skipped\n")


def test_cli_text_report_and_strict_mode(tmp_path: Path) -> None:
    path = write(tmp_path, safe(max_rows=None))
    code, output, error = cli("lint", "--policy", str(path))
    assert code == 0 and not error
    assert output == (
        "WARN max-rows-disabled: max_rows is null, so queries have no row limit\n"
        "  Why: One query can copy out a whole table, and huge results are slow and costly for "
        "the database and the model.\n"
        "  Fix: Set max_rows to the most rows one answer needs, such as 1000 (the default).\n"
        "  See: https://jmmana.github.io/sqlguardllm/policy-reference/#max_rows\n"
        "1 warning, 0 suppressed, 0 checks skipped\n"
    )
    code, _, _ = cli("lint", "--policy", str(path), "--strict")
    assert code == 1
    clean = write(tmp_path, safe())
    assert cli("lint", "--policy", str(clean), "--strict")[0] == 0


def test_cli_json_report_in_spanish(tmp_path: Path) -> None:
    path = write(tmp_path, safe(schema=None, max_rows=None, aggregate_only={"orders": 2}))
    code, output, error = cli("lint", "--policy", str(path), "--format", "json", "--language", "es")
    assert code == 0 and not error
    data = json.loads(output)
    assert set(data) == {"warnings", "suppressed", "skipped"}
    first = data["warnings"][0]
    assert first == {
        "code": "max-rows-disabled",
        "roles": None,
        "message": "max_rows es null, así que las consultas no tienen límite de filas",
        "explanation": first["explanation"],
        "fix": first["fix"],
        "reference": "https://jmmana.github.io/sqlguardllm/policy-reference/#max_rows",
    }
    assert "aggregate_only deja que orders devuelva grupos de 2 filas" in output
    assert data["skipped"][0]["reason"].startswith("necesita el schema de la política")


def test_cli_spanish_text_summary(tmp_path: Path) -> None:
    path = write(tmp_path, safe(schema=None))
    code, output, _ = cli("lint", "--policy", str(path), "--language", "es")
    assert code == 0
    assert output.endswith("0 avisos, 0 suprimidos, 2 comprobaciones omitidas\n")


def test_cli_json_lists_roles_for_policy_sets(tmp_path: Path) -> None:
    path = write(tmp_path, {"base": safe(), "roles": {"auditor": {"row_filters": {}}}})
    code, output, _ = cli("lint", "--policy", str(path), "--format", "json", "--strict")
    assert code == 1
    data = json.loads(output)
    assert [item["roles"] for item in data["warnings"]] == [["auditor"], ["auditor"]]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ({"dialect": "postgres", "tabels": []}, "unknown policy keys"),
        ({"dialect": "postgres", "tables": 5}, "invalid policy"),
        ({"dialect": "nope"}, "unknown dialect"),
        ({"base": {"dialect": "postgres"}, "roles": {"a": {"extends": "b"}}}, "unknown role"),
        ({"dialect": "postgres", "lint_suppress": {"nope": "x"}}, "unknown lint code"),
    ],
)
def test_cli_invalid_policies_are_usage_errors(
    tmp_path: Path, content: dict[str, Any], message: str
) -> None:
    code, output, error = cli("lint", "--policy", str(write(tmp_path, content)))
    assert code == 2 and not output
    assert message in error


def test_cli_usage_errors(tmp_path: Path) -> None:
    code, output, error = cli("lint", "--policy", str(tmp_path / "missing.yaml"))
    assert code == 2 and not output and "cannot read the policy" in error
    path = write(tmp_path, safe())
    code, output, error = cli("lint", "--policy", str(path), "--max-rows-threshold", "0")
    assert code == 2 and not output and "--max-rows-threshold" in error


def test_yaml_policy_with_suppression_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump(safe(max_rows=None, lint_suppress={"max-rows-disabled": "Batch jobs."})),
        encoding="utf-8",
    )
    code, output, _ = cli("lint", "--policy", str(path), "--strict")
    assert code == 0
    assert output.endswith("0 warnings, 1 suppressed, 0 checks skipped\n")
