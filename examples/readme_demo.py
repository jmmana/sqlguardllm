"""Capture real Guard results and execute accepted reads on a synthetic SQLite DB.

    python examples/readme_demo.py --language en
    python examples/readme_demo.py --language es --out docs/assets/demo-trace.es.json

Validation uses the PostgreSQL dialect. Only the accepted, portable SELECT
statements are executed on SQLite; denied SQL is never passed to the executor.
The research harness separately tests SQLite, DuckDB and PostgreSQL/WASM.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlguardllm import Guard, Policy

CONTEXT = {"user": {"tenant_id": 7}}
QUERIES = (
    ("allowed", "SELECT id, total FROM invoices ORDER BY id", CONTEXT),
    (
        "write",
        "WITH d AS (DELETE FROM invoices RETURNING *) SELECT * FROM d",
        CONTEXT,
    ),
    ("private_column", "SELECT email FROM customers", CONTEXT),
    (
        "cross_tenant",
        "SELECT id, total FROM invoices WHERE tenant_id = 999 OR 1 = 1 ORDER BY id",
        CONTEXT,
    ),
    ("harmless_literal", "SELECT 'DROP TABLE invoices' AS note", CONTEXT),
    ("missing_context", "SELECT id, total FROM invoices", {}),
)


def capture(language: str = "en") -> dict[str, Any]:
    guard = Guard(
        Policy(
            dialect="postgres",
            tables={"invoices", "customers"},
            hidden_columns={"customers.email"},
            row_filters={"invoices": "tenant_id = {user.tenant_id}"},
            max_rows=100,
        ),
        language=language,
    )
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE invoices (id INTEGER, tenant_id INTEGER, total INTEGER);"
        "INSERT INTO invoices VALUES (1, 7, 250), (2, 999, 900), (3, 7, 120);"
        "CREATE TABLE customers (id INTEGER, email TEXT);"
        "INSERT INTO customers VALUES (1, 'synthetic@example.invalid');"
    )
    db.execute("PRAGMA query_only = ON")
    cases = []
    try:
        for name, sql, context in QUERIES:
            result = guard.check(sql, context=context)
            item = {
                "id": name,
                "input": sql,
                "ok": result.ok,
                "sql": result.sql,
                "tables": sorted(result.tables),
                "applied_filters": list(result.applied_filters),
                "violations": [
                    {"code": v.code.value, "message": v.message, "detail": v.detail}
                    for v in result.violations
                ],
                "executed": False,
                "rows": [],
            }
            if result.ok:
                assert result.sql is not None
                item["rows"] = [list(row) for row in db.execute(result.sql).fetchall()]
                item["executed"] = True
            cases.append(item)
        assert cases[0]["rows"] == [[1, 250], [3, 120]]
        assert cases[3]["rows"] == cases[0]["rows"]
        assert not cases[1]["executed"] and not cases[2]["executed"]
        assert db.execute("SELECT COUNT(*) FROM invoices").fetchone() == (3,)
    finally:
        db.close()
    return {
        "language": language,
        "validation_dialect": "postgres",
        "execution_engine": "sqlite",
        "synthetic_data": True,
        "tenant_id": 7,
        "max_rows": 100,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "es"), default="en")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output = json.dumps(capture(args.language), indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
