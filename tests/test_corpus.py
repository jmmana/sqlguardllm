"""Runs every case in tests/corpus/*.yaml. See docs/ATTACK_GALLERY.md to add one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import CONTEXT, standard_policy
from sqlguard import Guard, ViolationCode

CORPUS = Path(__file__).parent / "corpus"
KEYS = {
    "id",
    "dialect",
    "sql",
    "expect",
    "codes",
    "contains",
    "not_contains",
    "context",
    "note",
    "pending",
}
DIALECTS = {
    "postgres",
    "mysql",
    "tsql",
    "oracle",
    "sqlite",
    "duckdb",
    "snowflake",
    "bigquery",
    "trino",
    "clickhouse",
}
CODES = {code.value for code in ViolationCode}


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for file in sorted(CORPUS.glob("*.yaml")):
        for case in yaml.safe_load(file.read_text(encoding="utf-8")) or []:
            case["_file"] = file.name
            cases.append(case)
    return cases


CASES = load_cases()


def test_corpus_is_well_formed() -> None:
    ids = [case.get("id") for case in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for case in CASES:
        where = f"{case['_file']}:{case.get('id')}"
        assert set(case) - {"_file"} <= KEYS, (
            f"{where}: unknown keys {set(case) - KEYS - {'_file'}}"
        )
        assert case.get("id") and case.get("sql") is not None, f"{where}: id and sql are required"
        assert case.get("dialect") in DIALECTS, f"{where}: unknown dialect"
        assert case.get("expect") in ("allow", "reject"), f"{where}: expect must be allow or reject"
        assert set(case.get("codes", [])) <= CODES, f"{where}: unknown codes"


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_case(case: dict[str, Any]) -> None:
    if case.get("pending"):
        pytest.skip(f"pending: {case['pending']}")
    guard = Guard(standard_policy(case["dialect"]))
    result = guard.check(case["sql"], case.get("context", CONTEXT))
    if case["expect"] == "reject":
        assert not result.ok, f"accepted: {result.sql}"
        expected = {ViolationCode(code) for code in case.get("codes", [])}
        assert expected <= result.codes, f"got {sorted(c.value for c in result.codes)}"
    else:
        assert result.ok, f"rejected: {result.messages}"
        assert result.sql is not None
        for fragment in case.get("contains", []):
            assert fragment in result.sql, f"{fragment!r} not in {result.sql}"
        for fragment in case.get("not_contains", []):
            assert fragment not in result.sql, f"{fragment!r} in {result.sql}"
