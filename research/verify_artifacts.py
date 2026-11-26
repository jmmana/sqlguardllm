"""Check measured artifacts and construct the self-contained arXiv source ZIP."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
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
    revision = data["metadata"]["base_commit"]
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "src/sqlguardllm"], text=True
    ).splitlines()
    for path in sorted(p for p in paths if p.endswith(".py")):
        digest.update(path.encode())
        digest.update(subprocess.check_output(["git", "show", f"{revision}:{path}"]))
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
    core = {"sqlite", "duckdb", "postgres_pglite", "postgres_service"}
    expanded = []
    generated = []
    for folder in ("results-review", "results-native-service"):
        directory = ROOT / "research" / folder
        expanded.extend(
            r for r in json.loads((directory / "original.json").read_text()) if r["engine"] in core
        )
        draws = json.loads(gzip.decompress((directory / "generated.json.gz").read_bytes()))
        generated.extend(r for r in draws if r["engine"] in core)
        for row in json.loads((directory / "edges.json").read_text()):
            if not row["execute"]:
                assert not row.get("executed")
    assert len(expanded) == 2112
    assert Counter(r["outcome"] for r in expanded if r["method"] == "sqlguardllm") == {
        "authorized_match": 288,
        "blocked": 96,
    }
    assert all(
        "permission denied" in r.get("error", "").lower()
        for r in expanded
        if r["method"] == "native_rls_columns" and r["outcome"] == "blocked"
    )
    assert len(generated) == 20000
    for engine in core:
        selected = [r for r in generated if r["engine"] == engine]
        assert len(selected) == 5000
        assert len({r["sql"] for r in selected}) == 3434
        assert Counter(r["outcome"] for r in selected) == {
            "authorized_match": 3888,
            "blocked": 685,
            "over_rejected": 233,
            "execution_error": 194,
        }
    timing = json.loads((ROOT / "research/results-review/timing.json").read_text())
    assert all(r["template_clusters"] == 48 for r in timing["bootstrap"])
    planner = json.loads((ROOT / "research/results-native-service/planner.json").read_text())
    assert planner["rows"] == 1000000 and len(planner["observations"]) == 90
    assert all(r["index_conditions"] and not r["subquery_scan"] for r in planner["summaries"])
    paper = ROOT / "research/paper"
    files = sorted(
        {
            *(p.name for p in paper.glob("*.tex")),
            "architecture.pdf",
            "outcomes.pdf",
            "latency.pdf",
            "heatmap.pdf",
            "acmart.cls",
            "acmart.dtx",
            "acmart.ins",
            "ACM-LICENSE.txt",
            "references.bib",
        }
    )
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
            *(ROOT / "research/results-review").glob("*.json*"),
            *(ROOT / "research/results-native-service").glob("*.json*"),
            ROOT / "research/datasets/spider-pilot.json",
            *(paper / name for name in files),
            paper / "main.pdf",
            *media,
            archive,
        ]
    }
    (ROOT / "research/artifact-sha256.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n"
    )
    print(f"Verified {len(data['execution'])} original and {len(expanded)} revised observations")
    print("Verified 20,000 core generated draws, preserved utility failures and native plans")
    print(f"Prepared {archive.name} ({archive.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
