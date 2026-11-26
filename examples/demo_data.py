"""A tiny sales database shared by the recipes in this folder.

Three branches, two customers and three invoices in SQLite. The signed-in user manages
branches 1 and 2, so they may see invoices 10 and 11 (350.0 in total) but never invoice 12,
and never a customer's email.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sqlguardllm import Policy

POLICY = Policy(
    dialect="sqlite",
    tables={"branches", "customers", "invoices"},
    hidden_columns={"customers.email"},
    # {user.branches} comes from the signed-in user, never from the model.
    row_filters={"invoices": "branch_id IN ({user.branches})"},
    max_rows=100,
)

# What the application knows about the signed-in user.
CONTEXT: dict[str, Any] = {"user": {"branches": [1, 2]}}

# SQL a model might write: one fine query and two it must never run.
ALLOWED_SQL = "SELECT SUM(total) AS revenue FROM invoices"
HIDDEN_COLUMN_SQL = "SELECT name, email FROM customers"
WRITE_SQL = "DELETE FROM invoices"

SCRIPT = """
CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches (id),
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    total REAL NOT NULL
);
INSERT INTO branches VALUES (1, 'North'), (2, 'South'), (3, 'East');
INSERT INTO customers VALUES (1, 'Ana', 'ana@example.com'), (2, 'Luis', 'luis@example.com');
INSERT INTO invoices VALUES (10, 1, 1, 100.0), (11, 2, 2, 250.0), (12, 3, 1, 900.0);
"""


def create_demo_database(path: str | Path) -> Path:
    """Write the demo database to ``path`` (replacing it) and return the path."""
    path = Path(path)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCRIPT)
    conn.close()
    return path


def connect_read_only(path: str | Path) -> sqlite3.Connection:
    """SQLite's stand-in for a read-only database user, the layer under sqlguardllm."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
