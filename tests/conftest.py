from __future__ import annotations

from typing import Any

import pytest

from sqlguardllm import Guard, Policy

TABLES = {"invoices", "invoice_lines", "branches", "customers", "products"}
HIDDEN = {"customers.email", "customers.tax_id"}
FILTERS = {"invoices": "branch_id IN ({user.branches})"}
CONTEXT: dict[str, Any] = {"user": {"branches": [1, 2]}}


def standard_policy(dialect: str = "postgres", **overrides: Any) -> Policy:
    """The policy used by the corpus and most tests."""
    values: dict[str, Any] = {
        "dialect": dialect,
        "tables": TABLES,
        "hidden_columns": HIDDEN,
        "row_filters": FILTERS,
        "max_rows": 1000,
        "default_schema": "dbo" if dialect == "tsql" else None,
    }
    values.update(overrides)
    return Policy(**values)


@pytest.fixture
def guard() -> Guard:
    return Guard(standard_policy())
