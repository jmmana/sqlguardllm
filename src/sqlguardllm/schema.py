"""JSON Schemas of policy and policy-set files, for editors and CI.

The published copies live in ``schema/`` and are regenerated with
``sqlguardllm schema > schema/policy.schema.json`` and
``sqlguardllm schema --policy-set > schema/policy-set.schema.json``; a test keeps them in sync.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from sqlguardllm.policy import Policy

SCHEMA_ID = "https://raw.githubusercontent.com/jmmana/sqlguardllm/main/schema/policy.schema.json"
POLICY_SET_SCHEMA_ID = (
    "https://raw.githubusercontent.com/jmmana/sqlguardllm/main/schema/policy-set.schema.json"
)

_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string", "minLength": 1}}
_NULLABLE_STRINGS: dict[str, Any] = {"type": ["array", "null"], "items": {"type": "string"}}
_LIMIT: dict[str, Any] = {"type": ["integer", "null"], "minimum": 0}

_PROPERTIES: dict[str, dict[str, Any]] = {
    "dialect": {
        "type": "string",
        "minLength": 1,
        "description": "Dialect of the SQL to check: postgres, mysql, tsql, oracle, sqlite…",
    },
    "tables": {
        **_NULLABLE_STRINGS,
        "description": "Allowlisted tables, optionally qualified (sales.customers). null "
        "disables the allowlist, which is not recommended.",
    },
    "hidden_columns": {
        **_STRINGS,
        "description": "Columns that may never be referenced, as table.column.",
    },
    "row_filters": {
        "type": "object",
        "additionalProperties": {"type": "string", "minLength": 1},
        "description": "Table → boolean SQL condition with {context.path} placeholders.",
    },
    "max_rows": {
        "type": ["integer", "null"],
        "minimum": 1,
        "description": "Maximum rows a query may return. null disables the limit.",
    },
    "default_schema": {
        "type": ["string", "null"],
        "description": "Schema assumed for unqualified table references.",
    },
    "denied_functions": {
        **_NULLABLE_STRINGS,
        "description": "Replaces the built-in denylist for the dialect when given.",
    },
    "extra_denied_functions": {**_STRINGS, "description": "Added to the denylist."},
    "function_policy": {
        "enum": ["denylist", "allowlist"],
        "description": "denylist (default) or allowlist.",
    },
    "allowed_functions": {
        **_NULLABLE_STRINGS,
        "description": 'Functions allowed when function_policy is "allowlist".',
    },
    "max_joins": {**_LIMIT, "description": "Maximum joins in the whole query."},
    "max_subquery_depth": {
        **_LIMIT,
        "description": "Maximum nesting of subqueries, derived tables and CTEs.",
    },
    "forbid_cartesian": {
        "type": "boolean",
        "description": "Reject CROSS JOIN and comma joins with no linking equality in WHERE.",
    },
    "required_filters": {
        "type": "object",
        "additionalProperties": {**_STRINGS, "minItems": 1},
        "description": "Table → columns; every read of the table must filter on one of them.",
    },
    "output_dialect": {
        "type": ["string", "null"],
        "description": "Generate the rewritten SQL in this dialect instead of dialect.",
    },
    "masked_columns": {
        "type": "object",
        "additionalProperties": {"enum": ["null", "redact", "hash", "partial"]},
        "description": "table.column → mask. Selectable but masked; never usable in filters, "
        "joins, grouping or ordering.",
    },
    "aggregate_only": {
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 2},
        "description": "Table → minimum group size k: only aggregates, groups of at least k rows.",
    },
    "schema": {
        "type": ["object", "null"],
        "additionalProperties": {**_STRINGS, "minItems": 1},
        "description": "Table → columns. Lets * over a table with masked columns be expanded.",
    },
}


def policy_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of a policy file."""
    names = [f.name for f in fields(Policy)]
    missing = sorted(set(names) - set(_PROPERTIES))
    if missing:  # pragma: no cover - guarded by tests
        raise RuntimeError(f"schema is missing policy fields: {', '.join(missing)}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "sqlguardllm policy",
        "description": "What SQL written by an untrusted source may do. "
        "See https://github.com/jmmana/sqlguardllm/blob/main/docs/DESIGN.md",
        "type": "object",
        "required": ["dialect"],
        "additionalProperties": False,
        "properties": {name: _PROPERTIES[name] for name in names},
    }


def policy_set_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of a policy-set file: ``base`` plus ``roles``.

    Built from :func:`policy_schema`, so new policy keys show up here too. Under ``add``,
    every list or mapping key of a policy is accepted.
    """
    policy = {key: value for key, value in policy_schema().items() if key not in ("$schema", "$id")}
    properties: dict[str, dict[str, Any]] = policy["properties"]
    additions: dict[str, dict[str, Any]] = {}
    for name, prop in properties.items():
        kinds = prop.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        for kind in ("array", "object"):
            if kind in kinds:
                additions[name] = {
                    **prop,
                    "type": kind,
                    "description": f"Added to the inherited {name}.",
                }
    role = {
        "type": ["object", "null"],
        "description": "Changes to the parent policy: a key set here replaces the inherited "
        "value; keys under add extend inherited lists and mappings.",
        "additionalProperties": False,
        "properties": {
            "extends": {
                "type": "string",
                "minLength": 1,
                "description": "Role to start from. Without it, the role starts from base.",
            },
            "add": {
                "type": "object",
                "additionalProperties": False,
                "properties": additions,
                "description": "Joined to inherited lists, merged into inherited mappings.",
            },
            **properties,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": POLICY_SET_SCHEMA_ID,
        "title": "sqlguardllm policy set",
        "description": "A base policy and roles that change it. "
        "See https://github.com/jmmana/sqlguardllm/blob/main/docs/DESIGN.md",
        "type": "object",
        "required": ["base"],
        "additionalProperties": False,
        "properties": {
            "base": {"$ref": "#/$defs/policy", "description": "The policy every role starts from."},
            "roles": {
                "type": "object",
                "propertyNames": {"minLength": 1},
                "additionalProperties": {"$ref": "#/$defs/role"},
                "description": "Role name → changes to its parent policy.",
            },
        },
        "$defs": {"policy": policy, "role": role},
    }
