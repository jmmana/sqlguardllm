"""Row filters: rewrites verified by executing the SQL on SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

import pytest

from sqlguardllm import Guard, Policy, ViolationCode


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE branches (id INTEGER, name TEXT);
        CREATE TABLE invoices (id INTEGER, branch_id INTEGER, owner TEXT, total REAL);
        INSERT INTO branches VALUES (1, 'North'), (2, 'South'), (3, 'East');
        INSERT INTO invoices VALUES
            (10, 1, 'ana', 100), (11, 2, 'luis', 200),
            (12, 3, 'O''Brien', 300), (13, 3, 'ana', 400);
        """
    )
    return conn


def sqlite_guard(filters: dict[str, str]) -> Guard:
    return Guard(
        Policy(dialect="sqlite", tables={"invoices", "branches"}, row_filters=filters, max_rows=100)
    )


QUERIES = [
    "SELECT SUM(total) FROM invoices",
    "SELECT b.name, SUM(i.total) FROM invoices i "
    "JOIN branches b ON b.id = i.branch_id GROUP BY b.name",
    "SELECT SUM(total) FROM (SELECT * FROM invoices) x",
    "WITH recent AS (SELECT * FROM invoices WHERE total > 0) SELECT SUM(total) FROM recent",
    "SELECT SUM(t) FROM (SELECT total AS t FROM invoices UNION ALL SELECT 0 FROM branches)",
    "SELECT name FROM branches WHERE id IN (SELECT branch_id FROM invoices)",
]


@pytest.mark.parametrize("sql", QUERIES)
def test_rows_outside_the_filter_never_come_back(db: sqlite3.Connection, sql: str) -> None:
    guard = sqlite_guard({"invoices": "branch_id IN ({user.branches})"})
    result = guard.check(sql, {"user": {"branches": [1, 2]}})
    assert result.ok, result.messages
    assert result.sql is not None
    rows = db.execute(result.sql).fetchall()
    unrestricted = db.execute(sql).fetchall()
    assert rows != unrestricted, "the filter changed nothing"
    filtered_db_sql = sql.replace(
        "FROM invoices", "FROM (SELECT * FROM invoices WHERE branch_id IN (1, 2))"
    )
    assert sorted(rows, key=repr) == sorted(db.execute(filtered_db_sql).fetchall(), key=repr)


def test_string_value_is_escaped(db: sqlite3.Connection) -> None:
    guard = sqlite_guard({"invoices": "owner = {user.name}"})
    result = guard.check("SELECT id FROM invoices", {"user": {"name": "O'Brien"}})
    assert result.ok and result.sql is not None
    assert db.execute(result.sql).fetchall() == [(12,)]


def test_injection_through_context_stays_a_literal(db: sqlite3.Connection) -> None:
    guard = sqlite_guard({"invoices": "owner = {user.name}"})
    result = guard.check("SELECT id FROM invoices", {"user": {"name": "x' OR '1'='1"}})
    assert result.ok and result.sql is not None
    assert db.execute(result.sql).fetchall() == []


def test_applied_filters_are_reported() -> None:
    guard = sqlite_guard({"invoices": "branch_id IN ({user.branches})"})
    result = guard.check(
        "SELECT a.id FROM invoices a JOIN invoices b ON a.id = b.id", {"user": {"branches": [3]}}
    )
    assert result.applied_filters == ("invoices: branch_id IN (3)", "invoices: branch_id IN (3)")


def test_attribute_and_nested_context() -> None:
    @dataclass
    class User:
        tenant: str

    guard = sqlite_guard({"invoices": "owner = {session.user.tenant}"})
    result = guard.check("SELECT id FROM invoices", {"session": {"user": User("ana")}})
    assert result.ok and result.sql is not None and "owner = 'ana'" in result.sql


def test_dates_and_none() -> None:
    guard = Guard(
        Policy(
            dialect="postgres",
            tables={"invoices"},
            row_filters={"invoices": "issued_on >= {since} AND voided IS {none}"},
        )
    )
    result = guard.check("SELECT id FROM invoices", {"since": date(2026, 1, 1), "none": None})
    assert result.ok and result.sql is not None
    assert "CAST('2026-01-01' AS DATE)" in result.sql and "IS NULL" in result.sql


@pytest.mark.parametrize(
    ("context", "code"),
    [
        ({}, ViolationCode.MISSING_CONTEXT),
        ({"user": {}}, ViolationCode.MISSING_CONTEXT),
        ({"user": {"branches": object()}}, ViolationCode.INVALID_CONTEXT),
        ({"user": {"branches": [float("nan")]}}, ViolationCode.INVALID_CONTEXT),
    ],
)
def test_context_problems_fail_closed(context: dict[str, object], code: ViolationCode) -> None:
    guard = sqlite_guard({"invoices": "branch_id IN ({user.branches})"})
    result = guard.check("SELECT id FROM invoices", context)
    assert not result.ok and result.codes == {code}


def test_list_outside_in_is_rejected() -> None:
    guard = sqlite_guard({"invoices": "branch_id = {user.branches}"})
    result = guard.check("SELECT id FROM invoices", {"user": {"branches": [1, 2]}})
    assert result.codes == {ViolationCode.INVALID_CONTEXT}


def test_messages_do_not_leak_context_values() -> None:
    guard = sqlite_guard({"invoices": "owner = {user.secret}"})
    result = guard.check("SELECT id FROM invoices", {"user": {"secret": object()}})
    assert all("object at" not in message for message in result.messages)
