"""Check measured artifacts and construct the self-contained arXiv source ZIP."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

from evaluate import summarize
from workload import manifest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research/results"


def main() -> None:
    data = {
        name: json.loads((RESULTS / f"{name}.json").read_text())
        for name in ("metadata", "execution", "latency", "summary", "regression", "workload")
    }
    digest = hashlib.sha256()
    for file in sorted((ROOT / "src/sqlguardllm").rglob("*.py")):
        digest.update(str(file.relative_to(ROOT)).encode())
        digest.update(file.read_bytes())
    assert digest.hexdigest() == data["metadata"]["engine_source_sha256"]
    assert (
        hashlib.sha256((ROOT / "research/workload.py").read_bytes()).hexdigest()
        == data["metadata"]["workload_sha256"]
    )
    assert data["workload"] == manifest()
    assert len(data["execution"]) == 1248
    assert all(len(row["samples_ns"]) == data["metadata"]["rounds"] for row in data["latency"])
    assert summarize(data["execution"], data["latency"]) == data["summary"]
    full = [row for row in data["execution"] if row["method"] == "sqlguardllm"]
    assert Counter(row["outcome"] for row in full) == {
        "authorized_match": 216,
        "blocked": 72,
    }
    assert all(row["outcome"] == "pass" for row in data["regression"])
    assert len(data["regression"]) == 148
    paper = ROOT / "research/paper"
    files = [
        "main.tex",
        "measurements.tex",
        "results-table.tex",
        "outcomes.pdf",
        "latency.pdf",
        "references.bib",
    ]
    archive = ROOT / "research/sqlguardllm-arxiv-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name in files:
            path = paper / name
            assert path.is_file() and path.stat().st_size > 0
            output.write(path, arcname=name)
    media = [
        path
        for language in ("en", "es")
        for path in (
            ROOT / f"docs/assets/demo.{language}.mp4",
            ROOT / f"docs/assets/demo.{language}.gif",
            ROOT / f"docs/assets/demo-trace.{language}.json",
        )
    ]
    hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            *(RESULTS.glob("*.json")),
            *(paper / name for name in files),
            paper / "main.pdf",
            *media,
            archive,
        ]
    }
    (ROOT / "research/artifact-sha256.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n"
    )
    print(f"Verified {len(data['execution'])} execution observations, 148 regression cases")
    print(f"Prepared {archive.name} ({archive.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
