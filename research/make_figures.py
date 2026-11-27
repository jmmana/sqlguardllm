"""Generate paper tables and figures only from the checked-in raw results."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research/results"
PAPER = ROOT / "research/original-figures"
LABELS = {
    "keyword_filter": "Keyword filter",
    "read_only_shape": "Read-only shape",
    "no_row_filters": "No row filters",
    "sqlguardllm": "sqlguardllm",
    "native_rls_columns": "Native RLS + grants",
}


def main() -> None:
    PAPER.mkdir(parents=True, exist_ok=True)
    summary = json.loads((RESULTS / "summary.json").read_text())
    records = json.loads((RESULTS / "execution.json").read_text())
    regression = json.loads((RESULTS / "regression.json").read_text())
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
        }
    )
    methods = [
        "keyword_filter",
        "read_only_shape",
        "no_row_filters",
        "sqlguardllm",
        "native_rls_columns",
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.3), layout="constrained")
    violation_rates, benign_rejections = [], []
    for method in methods:
        rows = [r for r in records if r["method"] == method]
        violation_rates.append(
            100 * sum(r["outcome"] == "policy_violation" for r in rows) / len(rows)
        )
        benign = [r for r in rows if r["kind"] == "benign"]
        benign_rejections.append(
            100 * sum(r["outcome"] == "over_rejected" for r in benign) / len(benign)
        )
    colors = ["#94a3b8", "#64748b", "#c084fc", "#10b981", "#2563eb"]
    for ax, values, title in zip(
        axes,
        (violation_rates, benign_rejections),
        ("Result-policy mismatches (%)", "Benign-query rejection (%)"),
        strict=True,
    ):
        ax.barh([LABELS[m] for m in methods], values, color=colors, height=0.62)
        ax.invert_yaxis()
        ax.set_xlim(0, 104)
        ax.set_title(title, loc="left", fontsize=11, weight="bold")
        ax.set_xlabel("Observed percentage in the authored workload")
        for i, value in enumerate(values):
            ax.text(value + 1, i, f"{value:.1f}", va="center", fontsize=9)
    fig.savefig(PAPER / "outcomes.pdf", bbox_inches="tight")
    fig.savefig(PAPER / "outcomes.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.5, 2.9), layout="constrained")
    full = [r for r in summary if r["method"] == "sqlguardllm"]
    names = [
        r["engine"]
        .replace("postgres_pglite", "PostgreSQL/WASM")
        .replace("sqlite", "SQLite")
        .replace("duckdb", "DuckDB")
        for r in full
    ]
    pos = list(range(len(full)))
    ax.bar(
        [p - 0.17 for p in pos],
        [r["validation_p50_ms"] for r in full],
        width=0.34,
        label="p50",
        color="#10b981",
    )
    ax.bar(
        [p + 0.17 for p in pos],
        [r["validation_p95_ms"] for r in full],
        width=0.34,
        label="p95",
        color="#2563eb",
    )
    ax.set_xticks(pos, names)
    ax.set_ylabel("Warm validation time (ms)")
    ax.set_title("Python validation only; database execution excluded", loc="left", fontsize=11)
    ax.legend(frameon=False)
    fig.savefig(PAPER / "latency.pdf", bbox_inches="tight")
    fig.savefig(PAPER / "latency.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    table = [
        r"\begin{tabular}{llrrrr}",
        r"\toprule",
        r"Runtime & Method & Match & Block & Mismatch & Other \\",
        r"\midrule",
    ]
    for row in summary:
        engine = {"sqlite": "SQLite", "duckdb": "DuckDB", "postgres_pglite": "PG/WASM"}[
            row["engine"]
        ]
        other = row.get("over_rejected", 0) + row.get("execution_error", 0)
        table.append(
            f"{engine} & {LABELS[row['method']]} & "
            f"{row.get('authorized_match', 0)} & {row.get('blocked', 0)} & "
            f"{row.get('policy_violation', 0)} & {other} " + r"\\"
        )
    table.extend((r"\bottomrule", r"\end{tabular}"))
    (PAPER / "results-table.tex").write_text("\n".join(table) + "\n")
    counts = Counter(r["outcome"] for r in regression)
    macros = [
        rf"\newcommand{{\RegressionPassed}}{{{counts['pass']}}}",
        rf"\newcommand{{\RegressionSkipped}}{{{counts['skipped']}}}",
    ]
    for row in full:
        key = {"sqlite": "Sqlite", "duckdb": "Duckdb", "postgres_pglite": "Pglite"}[row["engine"]]
        macros.extend(
            (
                rf"\newcommand{{\{key}Median}}{{{row['validation_p50_ms']:.3f}}}",
                rf"\newcommand{{\{key}Tail}}{{{row['validation_p95_ms']:.3f}}}",
            )
        )
    (PAPER / "measurements.tex").write_text("\n".join(macros) + "\n")
    print(json.dumps({"regression": counts, "full_guard": full}, indent=2))


if __name__ == "__main__":
    main()
