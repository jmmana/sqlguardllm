"""Aggregate-only tables, executed on SQLite: groups smaller than k never come back."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from sqlguardllm import Guard, Policy, PolicyError, ViolationCode

# Department sizes: sales 5, support 3, legal 1 (one person: their salary must never leak).
SALARIES = [
    *[(i, "sales", 1000 + i) for i in range(5)],
    *[(10 + i, "support", 2000 + i) for i in range(3)],
    (20, "legal", 9999),
]


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE salaries (person INT, dept TEXT, amount INT)")
    conn.executemany("INSERT INTO salaries VALUES (?, ?, ?)", SALARIES)
    yield conn
    conn.close()


def _guard(k: int = 3) -> Guard:
    return Guard(Policy(dialect="sqlite", tables={"salaries"}, aggregate_only={"salaries": k}))


def _run(db: sqlite3.Connection, sql: str, k: int = 3) -> list[tuple[object, ...]]:
    result = _guard(k).check(sql)
    assert result.ok, result.messages
    assert result.sql is not None
    return db.execute(result.sql).fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT dept, AVG(amount) FROM salaries GROUP BY dept",
        "SELECT dept, SUM(amount) AS total FROM salaries GROUP BY 1",
        "SELECT dept AS d, MAX(amount) FROM salaries GROUP BY d",
        "SELECT dept, AVG(amount) FROM salaries GROUP BY dept HAVING AVG(amount) > 0",
        "SELECT dept, MAX(amount) FROM salaries WHERE amount > 1500 GROUP BY dept",
    ],
)
def test_small_groups_never_come_back(db: sqlite3.Connection, sql: str) -> None:
    rows = _run(db, sql)
    departments = {row[0] for row in rows}
    assert "legal" not in departments
    assert 9999 not in {value for row in rows for value in row}


def test_existing_having_is_kept(db: sqlite3.Connection) -> None:
    sql = "SELECT dept, AVG(amount) FROM salaries GROUP BY dept HAVING AVG(amount) > 1500"
    assert {row[0] for row in _run(db, sql)} == {"support"}


def test_k_follows_the_policy(db: sqlite3.Connection) -> None:
    sql = "SELECT dept, COUNT(*) FROM salaries GROUP BY dept"
    assert {row[0] for row in _run(db, sql, k=4)} == {"sales"}
    assert _run(db, "SELECT COUNT(*) FROM salaries", k=20) == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM salaries",
        "SELECT person, amount FROM salaries",
        "SELECT dept, amount FROM salaries GROUP BY dept",
        "SELECT dept, AVG(amount) OVER (PARTITION BY dept) FROM salaries",
        "SELECT x.amount FROM (SELECT amount FROM salaries) x",
        "SELECT 1 WHERE 9999 IN (SELECT amount FROM salaries)",
    ],
)
def test_raw_reads_are_rejected(sql: str) -> None:
    result = _guard().check(sql)
    assert ViolationCode.AGGREGATE_REQUIRED in result.codes


def test_invalid_aggregate_policies() -> None:
    with pytest.raises(PolicyError, match="minimum group size"):
        Policy(dialect="sqlite", aggregate_only={"salaries": 1})
    with pytest.raises(PolicyError, match="not in the allowed tables"):
        Guard(Policy(dialect="sqlite", tables={"other"}, aggregate_only={"salaries": 3}))
