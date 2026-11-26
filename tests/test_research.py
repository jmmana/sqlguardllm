"""Smoke-test the independent reference and the row-filter ablation on SQLite."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_execution_study_detects_missing_tenant_filters(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "research/evaluate.py"),
            "--engines",
            "sqlite",
            "--rounds",
            "1",
            "--warmup",
            "0",
            "--out",
            str(tmp_path),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((tmp_path / "summary.json").read_text())
    full = next(row for row in summary if row["method"] == "sqlguardllm")
    ablation = next(row for row in summary if row["method"] == "no_row_filters")
    assert full["authorized_match"] == 72
    assert full["blocked"] == 24
    assert full.get("policy_violation", 0) == 0
    assert ablation["policy_violation"] > 0
    records = json.loads((tmp_path / "execution.json").read_text())
    assert {row["tenant"] for row in records} == {1, 2}
    assert {row["kind"] for row in records} == {"benign", "isolation", "forbidden"}


def test_review_workload_exposes_outer_where_and_surviving_mutants(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "research/review_eval.py"),
            "--engines",
            "sqlite",
            "--generated",
            "50",
            "--mutation-generated",
            "20",
            "--rounds",
            "1",
            "--out",
            str(tmp_path),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    records = json.loads((tmp_path / "original.json").read_text())
    full = [r for r in records if r["method"] == "sqlguardllm"]
    assert all(r["outcome"] in {"authorized_match", "blocked"} for r in full)
    assert any(r["method"] == "outer_where" and r["outcome"] == "result_mismatch" for r in records)
    mutants = json.loads((tmp_path / "mutations.json").read_text())
    assert len({r["mutant"] for r in mutants if r["killed"]}) == 7
    function_rows = [r for r in mutants if r["mutant"] == "remove_function_check"]
    assert not next(r for r in function_rows if r["suite"] == "original_48")["killed"]
    assert next(r for r in function_rows if r["suite"] == "review_edges")["killed"]
    edges = json.loads((tmp_path / "edges.json").read_text())
    assert all(not r.get("executed") for r in edges if not r["execute"])
