"""The benchmark in benchmarks/ keeps working; the timing itself runs in the Benchmark workflow."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from sqlguardllm import Guard

SCRIPT = Path(__file__).parents[1] / "benchmarks" / "bench_check.py"


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_case_is_accepted_or_rejected_as_declared(bench: ModuleType) -> None:
    guards = {name: Guard(policy) for name, policy in bench.policies().items()}
    assert len(bench.CASES) >= 20
    assert len({case.name for case in bench.CASES}) == len(bench.CASES)
    for case in bench.CASES:
        result = guards[case.policy].check(case.sql, bench.CONTEXT)
        assert result.ok == case.allowed, (case.name, result.messages)


def test_runs_and_writes_json(bench: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "head.json"
    args = ["--rounds", "1", "--per-round", "2", "--warmup", "0", "--no-budget"]
    assert bench.main([*args, "--json", str(out), "--summary", str(tmp_path / "s.md")]) == 0
    data = json.loads(out.read_text())
    assert [row["name"] for row in data["timings"]] == [case.name for case in bench.CASES]
    assert all(row["samples"] == 2 and row["median_ms"] > 0 for row in data["timings"])
    assert "| `large_report` | large |" in (tmp_path / "s.md").read_text()


def test_fails_over_budget(bench: ModuleType) -> None:
    cases = bench.CASES[:1]
    timings = bench.measure(cases, rounds=1, per_round=1, warmup=0, budget_ms=0.000001)
    assert timings[0].over_budget
    assert bench.main(["--rounds", "1", "--per-round", "1", "--budget-ms", "0.000001"]) == 1


def test_compare_flags_regressions_over_the_threshold(bench: ModuleType, tmp_path: Path) -> None:
    def save(name: str, medians: dict[str, float]) -> Path:
        path = tmp_path / name
        rows = [{"name": k, "median_ms": v} for k, v in medians.items()]
        path.write_text(json.dumps({"timings": rows}))
        return path

    base = [save("b1.json", {"a": 1.0, "b": 1.0}), save("b2.json", {"a": 1.1, "b": 0.9})]
    # The fastest median per side counts: a 1.0 -> 1.2 (+20 %), b 0.9 -> 1.2 (+33 %).
    head = [save("h1.json", {"a": 1.2, "b": 1.3, "new": 2.0}), save("h2.json", {"b": 1.2})]
    slower = bench.regressions(bench.load_medians(base), bench.load_medians(head), 0.25)
    assert [name for name, *_ in slower] == ["b"]
    args = ["compare", "--base", *map(str, base), "--head", *map(str, head)]
    assert bench.main(args) == 1
    assert bench.main([*args, "--threshold", "0.5"]) == 0
