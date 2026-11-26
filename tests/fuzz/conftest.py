"""Shared deterministic profiles, engine fixtures, and fuzz result persistence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from fuzz.strategies import COLUMNS, TABLES
from fuzz.support import (
    CONTEXT,
    ENGINES,
    EXAMPLES,
    PROFILE,
    REGRESSIONS,
    ROW_FILTERS,
    SEED,
    STATS,
    regression_id,
)
from sqlguardllm import Guard, Policy

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus" / "fuzz_regressions.yaml"
RESULTS = ROOT / "research" / "results" / "fuzz.json"
ENGINE_URLS = {
    "sqlite": "sqlite://",
    "duckdb": "duckdb:///:memory:",
    "postgres": os.environ.get("SQLGUARD_PG_URL"),
    "mysql": os.environ.get("SQLGUARD_MYSQL_URL"),
    "tsql": os.environ.get("SQLGUARD_MSSQL_URL"),
}


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    print(
        f"\nSQLGuard fuzz: profile={PROFILE} seed={SEED} "
        f"hypothesis_examples_per_engine={EXAMPLES} engines={','.join(ENGINES)}"
    )


@pytest.fixture(params=ENGINES)
def fuzz_engine(request: pytest.FixtureRequest) -> Any:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    name = request.param
    url = ENGINE_URLS[name]
    if not url:
        if os.environ.get("SQLGUARD_FUZZ_REQUIRE_ENGINES") == "1":
            pytest.fail(f"SQLGuard fuzz engine {name}: URL not set")
        pytest.skip(f"SQLGuard fuzz engine {name}: URL not set")
    if name == "duckdb":
        pytest.importorskip("duckdb_engine")

    engine = sqlalchemy.create_engine(url)
    columns = (
        "id VARCHAR(128), branch_id VARCHAR(128), related_id VARCHAR(128), label VARCHAR(128), "
        "payload VARCHAR(128), hidden_value VARCHAR(128), masked_value VARCHAR(128)"
    )
    tables = TABLES
    with engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            connection.exec_driver_sql(f"CREATE TABLE {table} ({columns})")
            for branch in (1, 2, 3):
                for row_index in (1, 2):
                    external = branch == 3
                    public_value = (
                        f"CANARY_TENANT3_{table}_{row_index}"
                        if external
                        else f"DATA_TENANT{branch}_{table}_{row_index}"
                    )
                    branch_value = (
                        "CANARY_TENANT3_BRANCH" if external else f"DATA_TENANT{branch}_BRANCH"
                    )
                    id_value = branch_value if table == "branches" else f"{public_value}_ID"
                    connection.exec_driver_sql(
                        f"INSERT INTO {table} VALUES "
                        f"('{id_value}', '{branch_value}', '{public_value}_RELATED', "
                        f"'{public_value}_LABEL', '{public_value}_PAYLOAD', "
                        f"'CANARY_HIDDEN_{table}_{branch}_{row_index}', "
                        f"'CANARY_MASKED_{table}_{branch}_{row_index}')"
                    )
    yield name, engine
    with engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    engine.dispose()


@pytest.fixture
def fuzz_guard(fuzz_engine: Any) -> Any:
    dialect, _ = fuzz_engine
    policy = Policy(
        dialect=dialect,
        tables=set(TABLES),
        hidden_columns={f"{table}.hidden_value" for table in TABLES},
        masked_columns={f"{table}.masked_value": "null" for table in TABLES},
        row_filters=ROW_FILTERS,
        schema={table: COLUMNS for table in TABLES},
        max_rows=4,
    )
    return Guard(policy)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    results = {
        "profile": PROFILE,
        "seed": SEED,
        "configured_examples_per_engine": EXAMPLES,
        "recorded_at": datetime.now(UTC).isoformat(),
        "engines": dict(sorted(STATS.items())),
    }
    RESULTS.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(stats["examples"] for stats in STATS.values())
    print(f"SQLGuard fuzz summary: seed={SEED} examples={total} engines={results['engines']}")

    if not REGRESSIONS:
        return
    cases = yaml.safe_load(CORPUS.read_text(encoding="utf-8")) if CORPUS.exists() else []
    known = {case["id"] for case in cases}
    for (dialect, invariant), sql in sorted(REGRESSIONS.items()):
        case_id = regression_id(dialect, invariant, sql)
        if case_id not in known:
            policy = {
                "tables": list(TABLES),
                "hidden_columns": [f"{table}.hidden_value" for table in TABLES],
                "masked_columns": {f"{table}.masked_value": "null" for table in TABLES},
                "row_filters": ROW_FILTERS,
                "schema": {table: list(COLUMNS) for table in TABLES},
                "max_rows": 4,
            }
            cases.append(
                {
                    "id": case_id,
                    "dialect": dialect,
                    "sql": sql,
                    "expect": (
                        "reject"
                        if invariant
                        in {
                            "other-tenant-canary",
                            "hidden-or-masked-canary",
                            "max-rows",
                            "unexpected-exception",
                        }
                        else "allow"
                    ),
                    "policy": policy,
                    "context": CONTEXT,
                    "note": f"Minimized Hypothesis regression for {invariant}.",
                }
            )
    CORPUS.write_text(yaml.safe_dump(cases, sort_keys=False, allow_unicode=True), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(ROOT / "tests" / "conformance" / "export_corpus.py")],
        cwd=ROOT,
        check=True,
    )
