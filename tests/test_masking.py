"""Masked columns, executed on SQLite: no real value ever comes back."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from conftest import standard_policy
from sqlguardllm import Guard, Policy, PolicyError, ViolationCode

PEOPLE = [
    (1, "Ana", "ana.rojas@acme.com", "900123456", "3001234567"),
    (2, "Luis", "luis.p@acme.com", "800987654", None),
    (3, "Eva", None, "700555444", "3109876543"),
]
SECRETS = {value for row in PEOPLE for value in row[2:] if value}
MASKS = {"customers.email": "partial", "customers.tax_id": "redact", "customers.phone": "null"}
SCHEMA = {"customers": ["id", "name", "email", "tax_id", "phone"]}


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE customers (id INT, name TEXT, email TEXT, tax_id TEXT, phone TEXT)")
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", PEOPLE)
    yield conn
    conn.close()


def _guard(**overrides: object) -> Guard:
    values: dict[str, object] = {"hidden_columns": set(), "masked_columns": MASKS}
    values.update(overrides)
    return Guard(standard_policy("sqlite", **values))


def _run(db: sqlite3.Connection, guard: Guard, sql: str) -> list[tuple[object, ...]]:
    result = guard.check(sql, {"user": {"branches": [1]}})
    assert result.ok, result.messages
    assert result.sql is not None
    return db.execute(result.sql).fetchall()


def _leaks(rows: list[tuple[object, ...]]) -> set[object]:
    return {value for row in rows for value in row} & SECRETS


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name, email, tax_id, phone FROM customers",
        "SELECT c.name, UPPER(c.email) AS contact, LENGTH(tax_id) FROM customers c",
        "SELECT CASE WHEN email LIKE 'ana%' THEN email END FROM customers",
        "SELECT x.e FROM (SELECT email AS e FROM customers) x",
        "WITH c AS (SELECT email, phone FROM customers) SELECT * FROM c",
        "SELECT name, ROW_NUMBER() OVER (ORDER BY email) FROM customers",
    ],
)
def test_no_real_value_comes_back(db: sqlite3.Connection, sql: str) -> None:
    assert _leaks(_run(db, _guard(), sql)) == set()


def test_strategies_and_output_names(db: sqlite3.Connection) -> None:
    guard = _guard()
    result = guard.check("SELECT id, email, tax_id, phone FROM customers ORDER BY id")
    assert result.sql is not None
    cursor = db.execute(result.sql)
    assert [d[0] for d in cursor.description] == ["id", "email", "tax_id", "phone"]
    assert cursor.fetchall() == [
        (1, "an***", "***", None),
        (2, "lu***", "***", None),
        (3, None, "***", None),
    ]


def test_star_is_expanded_with_the_schema(db: sqlite3.Connection) -> None:
    rows = _run(db, _guard(schema=SCHEMA), "SELECT * FROM customers")
    assert len(rows) == 3
    assert _leaks(rows) == set()
    assert rows[0][:2] == (1, "Ana")


@pytest.mark.parametrize("sql", ["SELECT * FROM customers", "SELECT c.* FROM customers c"])
def test_star_without_schema_is_rejected(sql: str) -> None:
    result = _guard().check(sql)
    assert result.codes == {ViolationCode.HIDDEN_COLUMN}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM customers WHERE email LIKE 'ana%'",
        "SELECT name FROM customers ORDER BY tax_id",
        "SELECT email, COUNT(*) FROM customers GROUP BY email",
        "SELECT COUNT(*) FROM customers HAVING MAX(phone) > '3'",
        "SELECT a.name FROM customers a JOIN customers b ON a.email = b.email",
        "SELECT a.name FROM customers a JOIN customers b USING (email)",
        "SELECT a.name FROM customers a NATURAL JOIN customers b",
        "SELECT name FROM customers WHERE id IN (SELECT id FROM customers WHERE tax_id = '1')",
        "SELECT c FROM customers c",
    ],
)
def test_masked_columns_outside_the_select_list_are_rejected(sql: str) -> None:
    result = _guard().check(sql)
    assert not result.ok
    assert ViolationCode.HIDDEN_COLUMN in result.codes


@pytest.mark.parametrize(
    ("dialect", "fragment"),
    [
        ("postgres", "ENCODE(SHA256(CONVERT_TO(CAST(email AS TEXT), 'UTF8')), 'hex') AS email"),
        ("mysql", "SHA2(CAST(email AS CHAR), 256) AS email"),
        ("tsql", "HASHBYTES('SHA2_256'"),
        ("oracle", "STANDARD_HASH(TO_CHAR(email), 'SHA256')"),
        ("snowflake", "SHA2(CAST(email AS VARCHAR), 256) AS email"),
        ("bigquery", "TO_HEX(SHA256(CAST(email AS STRING))) AS email"),
    ],
)
def test_hash_mask_per_dialect(dialect: str, fragment: str) -> None:
    guard = Guard(
        standard_policy(dialect, hidden_columns=set(), masked_columns={"customers.email": "hash"})
    )
    result = guard.check("SELECT email FROM customers")
    assert result.sql is not None
    assert fragment in result.sql


def test_hash_follows_the_output_dialect() -> None:
    policy = standard_policy(
        "postgres",
        hidden_columns=set(),
        masked_columns={"customers.email": "hash"},
        output_dialect="mysql",
    )
    result = Guard(policy).check("SELECT email FROM customers")
    assert result.sql is not None
    assert "SHA2(CAST(email AS CHAR), 256)" in result.sql


def test_hash_is_not_available_on_sqlite() -> None:
    with pytest.raises(PolicyError, match="hash mask is not available for sqlite"):
        Guard(
            standard_policy(
                "sqlite", hidden_columns=set(), masked_columns={"customers.email": "hash"}
            )
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"masked_columns": {"customers.name": "scramble"}}, "masked_columns"),
        ({"masked_columns": {"customers.email": "null"}}, "both hidden and masked"),
        ({"masked_columns": {"secrets.value": "null"}}, "outside the allowed tables"),
    ],
)
def test_invalid_mask_policies(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        Guard(standard_policy("postgres", **kwargs))


def test_masks_and_row_filters_together(db: sqlite3.Connection) -> None:
    policy = Policy(
        dialect="sqlite",
        tables={"customers"},
        masked_columns={"customers.email": "redact"},
        row_filters={"customers": "id IN ({user.ids})"},
    )
    result = Guard(policy).check("SELECT id, email FROM customers", {"user": {"ids": [1, 3]}})
    assert result.sql is not None
    assert db.execute(result.sql).fetchall() == [(1, "***"), (3, "***")]
