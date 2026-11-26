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
