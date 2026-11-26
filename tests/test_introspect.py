from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlguardllm import Guard, Policy, PolicyError


@pytest.fixture
def url(tmp_path: Path) -> str:
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE invoices (id INT, total INT, customer_id INT);
        CREATE TABLE customers (id INT, name TEXT, email TEXT);
        CREATE TABLE audit_log (id INT, who TEXT);
        CREATE VIEW recent AS SELECT id FROM invoices;
        """
    )
    conn.close()
    return f"sqlite:///{path}"


def test_reads_tables_and_columns(url: str) -> None:
    policy = Policy.from_database(url)
    assert policy.dialect == "sqlite"
    assert set(policy.tables or ()) == {"invoices", "customers", "audit_log", "recent"}
    assert policy.schema is not None
    assert policy.schema["customers"] == ("id", "name", "email")


def test_include_and_exclude_are_globs(url: str) -> None:
    only = Policy.from_database(url, include=["inv*", "cust*"])
    assert set(only.tables or ()) == {"invoices", "customers"}
    skipped = Policy.from_database(url, exclude=["audit_*", "recent"])
    assert set(skipped.tables or ()) == {"invoices", "customers"}


def test_other_options_pass_through_and_the_guard_uses_them(url: str) -> None:
    policy = Policy.from_database(url, exclude=["audit_*"], hidden_columns={"customers.email"})
    guard = Guard(policy)
    assert guard.check("SELECT total FROM invoices").ok
    assert not guard.check("SELECT email FROM customers").ok
    assert not guard.check("SELECT who FROM audit_log").ok


def test_star_over_masked_table_expands_with_the_read_schema(url: str) -> None:
    policy = Policy.from_database(url, masked_columns={"customers.email": "redact"})
    result = Guard(policy).check("SELECT * FROM customers")
    assert result.ok and result.sql is not None and "'***' AS email" in result.sql


def test_dialect_can_be_overridden(url: str) -> None:
    assert Policy.from_database(url, dialect="postgres").dialect == "postgres"


def test_nothing_matching_is_an_error(url: str) -> None:
    with pytest.raises(PolicyError, match="no table matched"):
        Policy.from_database(url, include=["nope*"])


def test_errors_never_carry_the_url(tmp_path: Path) -> None:
    secret_url = f"sqlite:///{tmp_path}/missing/dir/x.db?password=hunter2"
    with pytest.raises(PolicyError) as info:
        Policy.from_database(secret_url)
    assert "hunter2" not in str(info.value)
