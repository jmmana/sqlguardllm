"""Writes conformance/cases.json: every corpus case, grouped by policy, with the in-process answer.

Run `uv run python tests/conformance/export_corpus.py` after changing tests/corpus/.
The Python, Node and .NET harnesses start one `sqlguardllm serve` per group and must get the
same `expected` (ok, sql, codes) as `Guard.check`. See docs/CONFORMANCE.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import CONTEXT, FILTERS, HIDDEN, TABLES  # noqa: E402
from sqlguardllm import Guard, Policy  # noqa: E402

OUT = ROOT / "conformance" / "cases.json"


def policy_dict(dialect: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """The JSON form of tests/conftest.py `standard_policy` (what `serve --policy` reads)."""
    values: dict[str, Any] = {
        "dialect": dialect,
        "tables": sorted(TABLES),
        "hidden_columns": sorted(HIDDEN),
        "row_filters": FILTERS,
        "max_rows": 1000,
    }
    if dialect == "tsql":
        values["default_schema"] = "dbo"
    values.update(overrides)
    return values


def build() -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for file in sorted((ROOT / "tests" / "corpus").glob("*.yaml")):
        for case in yaml.safe_load(file.read_text(encoding="utf-8")) or []:
            if case.get("pending"):
                continue
            policy = policy_dict(case["dialect"], case.get("policy", {}))
            key = json.dumps(policy, sort_keys=True)
            group = groups.setdefault(key, {"policy": policy, "cases": []})
            context = case.get("context", CONTEXT)
            result = Guard(Policy.from_dict(policy)).check(case["sql"], context)
            group["cases"].append(
                {
                    "id": case["id"],
                    "sql": case["sql"],
                    "context": context,
                    "expect": case["expect"],
                    "codes": case.get("codes", []),
                    "contains": case.get("contains", []),
                    "not_contains": case.get("not_contains", []),
                    "expected": {
                        "ok": result.ok,
                        "sql": result.sql,
                        "codes": sorted(c.value for c in result.codes),
                    },
                }
            )
    ordered = sorted(groups.values(), key=lambda g: json.dumps(g["policy"], sort_keys=True))
    for index, group in enumerate(ordered):
        group["name"] = f"g{index:02d}-{group['policy']['dialect']}"
    return {"groups": ordered}


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    data = json.loads(OUT.read_text(encoding="utf-8"))
    print(f"{sum(len(g['cases']) for g in data['groups'])} cases in {len(data['groups'])} groups")
