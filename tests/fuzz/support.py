"""Shared deterministic fuzz settings, metrics, and minimized regression storage."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from typing import Any

PROFILE = os.environ.get("SQLGUARD_FUZZ_PROFILE", "fast")
SEED = int(os.environ.get("SQLGUARD_FUZZ_SEED", "20261126"))
EXAMPLES = int(os.environ.get("SQLGUARD_FUZZ_EXAMPLES", "300" if PROFILE == "fast" else "20000"))
DEFAULT_ENGINES = (
    ("sqlite", "duckdb")
    if PROFILE == "fast"
    else (
        "sqlite",
        "duckdb",
        "postgres",
        "mysql",
        "tsql",
    )
)
ENGINES = tuple(
    name
    for name in os.environ.get("SQLGUARD_FUZZ_ENGINES", ",".join(DEFAULT_ENGINES)).split(",")
    if name
)
ROW_FILTERS = {
    table: ("id IN ({user.branches})" if table == "branches" else "branch_id IN ({user.branches})")
    for table in ("invoices", "invoice_lines", "branches", "customers", "products")
}
CONTEXT = {"user": {"branches": [f"DATA_TENANT{branch}_BRANCH" for branch in (1, 2)]}}
STATS: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"examples": 0, "allowed": 0, "rejected_by_rule": {}, "invariant_failures": 0}
)
REGRESSIONS: dict[tuple[str, str], str] = {}


def record_result(engine: str, ok: bool, codes: set[str]) -> None:
    stats = STATS[engine]
    stats["examples"] += 1
    if ok:
        stats["allowed"] += 1
    else:
        rejected = stats["rejected_by_rule"]
        for code in codes:
            rejected[code] = rejected.get(code, 0) + 1


def record_failure(engine: str, dialect: str, sql: str, invariant: str) -> None:
    STATS[engine]["invariant_failures"] += 1
    key = (dialect, invariant)
    previous = REGRESSIONS.get(key)
    if previous is None or (len(sql), sql) < (len(previous), previous):
        REGRESSIONS[key] = sql


def regression_id(dialect: str, invariant: str, sql: str) -> str:
    digest = hashlib.sha256(f"{dialect}\0{invariant}\0{sql}".encode()).hexdigest()[:12]
    return f"fuzz-{digest}"
