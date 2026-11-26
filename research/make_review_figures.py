"""Generate the revised paper's vector figures, tables and numeric macros."""

from __future__ import annotations

import gzip
import json
import statistics
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from workload import cases

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "research/paper"
LOCAL = ROOT / "research/results-review"
NATIVE = ROOT / "research/results-native-service"
CORE = ("sqlite", "duckdb", "postgres_pglite", "postgres_service")
METHODS = (
    "keyword_filter",
    "read_only_shape",
    "no_row_filters",
    "outer_where",
    "sqlguardllm",
    "native_rls_columns",
)
LABELS = {
    "keyword_filter": "Keyword",
    "read_only_shape": "Query shape",
    "no_row_filters": "No row filters",
    "outer_where": "Outermost WHERE",
    "sqlguardllm": "sqlguardllm",
    "native_rls_columns": "Native RLS + grants",
    "parse_only": "SQLGlot parse only",
}
GREEN = "#009E73"
BLUE = "#0072B2"


def read(path):
    return json.loads(
        gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_text()
    )


def combined(name):
    return [r for r in read(LOCAL / name) if r.get("engine") in CORE] + read(NATIVE / name)


def save(fig, name):
    fig.savefig(PAPER / (name + ".pdf"), bbox_inches="tight")
    fig.savefig(PAPER / (name + ".png"), dpi=180, bbox_inches="tight")
    plt.close(fig)


def architecture():
    fig, ax = plt.subplots(figsize=(8.2, 3.7))
    ax.set(xlim=(0, 10), ylim=(0, 5.4))
    ax.axis("off")
    ax.add_patch(
        FancyBboxPatch(
            (0.08, 0.15),
            2.0,
            4.95,
            boxstyle="round,pad=.05",
            facecolor="#FCEDE9",
            edgecolor="#BA6C59",
            lw=1,
        )
    )
    ax.add_patch(
        FancyBboxPatch(
            (2.35, 0.15),
            7.5,
            4.95,
            boxstyle="round,pad=.05",
            facecolor="#F0F7F5",
            edgecolor=GREEN,
            lw=1,
        )
    )
    ax.text(1.08, 4.77, "UNTRUSTED", ha="center", weight="bold", fontsize=9, color="#994F3D")
    ax.text(
        6.1,
        4.77,
        "TRUSTED APPLICATION BOUNDARY",
        ha="center",
        weight="bold",
        fontsize=9,
        color=GREEN,
    )

    def box(x, y, w, h, label, *, color="white"):
        ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h, boxstyle="round,pad=.04", facecolor=color, edgecolor="#45515C", lw=0.8
            )
        )
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=8.5)

    def arrow(a, b, dashed=False):
        ax.add_patch(
            FancyArrowPatch(
                a,
                b,
                arrowstyle="-|>",
                mutation_scale=10,
                color="#45515C",
                lw=1,
                linestyle="--" if dashed else "-",
            )
        )

    box(0.3, 3.3, 1.5, 0.85, "User / LLM")
    box(0.3, 1.95, 1.5, 0.8, "SQL string")
    arrow((1.05, 3.3), (1.05, 2.8))
    box(2.65, 2.0, 1.7, 0.9, "Parse + validate\nstatement / functions")
    box(4.9, 2.0, 1.85, 0.9, "Resolve physical\ntables + columns")
    box(7.3, 2.0, 2.1, 0.9, "Rewrite every source\n+ cap + regenerate")
    arrow((1.85, 2.37), (2.6, 2.37))
    arrow((4.4, 2.4), (4.85, 2.4))
    arrow((6.8, 2.4), (7.25, 2.4))
    box(4.8, 3.65, 3.05, 0.65, "Policy + authenticated context", color="#E0F0EB")
    arrow((5.8, 3.6), (5.8, 2.95), True)
    arrow((7.4, 3.6), (8.3, 2.95), True)
    box(7.3, 0.5, 2.1, 0.75, "Executor + database")
    arrow((8.35, 1.95), (8.35, 1.3))
    box(4.85, 0.5, 1.9, 0.75, "DB credentials", color="#E0F0EB")
    arrow((6.8, 0.88), (7.25, 0.88), True)
    ax.text(3.55, 1.47, "Reject → structured violations", ha="center", fontsize=8, color="#994F3D")
    ax.text(
        5.8,
        0.05,
        "Execute only the regenerated SQL returned after admission",
        ha="center",
        fontsize=8,
        color="#45515C",
    )
    save(fig, "architecture")


def outcomes(records):
    rows = [r for r in records if r["engine"] == "postgres_service"]
    fig, axes = plt.subplots(1, 3, figsize=(9.4, 2.85), layout="constrained")
    colors = {"Match": "#C6CDD2", "Block": "#737D87", "Mismatch": "#D55E00", "Other": "#E69F00"}
    for ax, kind, title in zip(
        axes,
        ("benign", "isolation", "forbidden"),
        ("Benign (48)", "Isolation stress (24)", "Forbidden (24)"),
        strict=True,
    ):
        for i, method in enumerate(METHODS):
            selected = [r for r in rows if r["method"] == method and r["kind"] == kind]
            c = Counter(r["outcome"] for r in selected)
            values = {
                "Match": c["authorized_match"],
                "Block": c["blocked"],
                "Mismatch": sum(
                    c[x] for x in ("unsafe_admission", "result_mismatch", "row_cap_violation")
                ),
                "Other": sum(
                    c[x]
                    for x in ("over_rejected", "execution_error", "guard_error", "oracle_error")
                ),
            }
            left = 0
            for label, value in values.items():
                ax.barh(
                    i,
                    value,
                    left=left,
                    color=colors[label],
                    height=0.66,
                    edgecolor=GREEN if method == "sqlguardllm" else "white",
                    linewidth=1,
                )
                if value:
                    ax.text(
                        left + value / 2,
                        i,
                        str(value),
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="white" if label in {"Block", "Mismatch"} else "#18212A",
                    )
                left += value
        ax.set_yticks(range(len(METHODS)), [LABELS[m] for m in METHODS] if ax is axes[0] else [])
        ax.invert_yaxis()
        ax.set_xlim(0, 48 if kind == "benign" else 24)
        ax.set_xticks([0, 24, 48] if kind == "benign" else [0, 12, 24])
        ax.set_title(title, fontsize=10, loc="left", weight="bold")
        ax.set_xlabel("Observations")
        if ax is axes[0]:
            ax.get_yticklabels()[4].set_color(GREEN)
            ax.get_yticklabels()[4].set_weight("bold")
    handles = [Rectangle((0, 0), 1, 1, color=color) for color in colors.values()]
    fig.legend(handles, list(colors), loc="outside lower center", ncol=4, frameon=False)
    save(fig, "outcomes")


def heatmap(records):
    ordered = cases(1)
    rows = [r for r in records if r["engine"] == "postgres_service"]
    values = np.array(
        [
            [
                sum(
                    r["outcome"] in {"authorized_match", "blocked"}
                    for r in rows
                    if r["id"] == p.id and r["method"] == m
                )
                for m in METHODS
            ]
            for p in ordered
        ]
    )
    fig, ax = plt.subplots(figsize=(7.5, 8.1), layout="constrained")
    ax.imshow(
        values,
        cmap=ListedColormap(["#F7E4DB", "#B8C9D3", "#71BBA6"]),
        vmin=0,
        vmax=2,
        aspect="auto",
    )
    labels = [
        p.id.replace("benign_", "B: ")
        .replace("isolation_", "I: ")
        .replace("forbidden_", "F: ")
        .replace("_", " ")
        for p in ordered
    ]
    ax.set_yticks(range(48), labels, fontsize=7.2)
    ax.set_xticks(range(len(METHODS)), [LABELS[m].replace(" ", "\n") for m in METHODS], fontsize=8)
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for i in range(48):
        for j in range(len(METHODS)):
            ax.text(
                j,
                i,
                str(values[i, j]) + "/2",
                ha="center",
                va="center",
                fontsize=6.7,
                color="#243039",
            )
    for y in (23.5, 35.5):
        ax.axhline(y, color="white", linewidth=3)
    ax.add_patch(Rectangle((3.5, -0.5), 1, 48, fill=False, edgecolor=GREEN, lw=2))
    ax.set_title("Correct outcomes across the two tenant contexts", fontsize=10, pad=43, loc="left")
    ax.set_xlabel("0/2: neither context correct · 1/2: one correct · 2/2: both correct", fontsize=8)
    save(fig, "heatmap")


def latency(timing):
    fig, axes = plt.subplots(1, 2, figsize=(8.5, 2.9), layout="constrained")
    colors = {
        "keyword_filter": "#B9BEC2",
        "read_only_shape": "#8D959C",
        "no_row_filters": "#5D6871",
        "outer_where": "#323E48",
        "sqlguardllm": GREEN,
        "parse_only": BLUE,
    }
    for method in (*METHODS[:-1], "parse_only"):
        values = sorted(r["median_ms"] for r in timing["samples"] if r["method"] == method)
        axes[0].step(
            values,
            np.arange(1, len(values) + 1) / len(values),
            where="post",
            label=LABELS[method],
            color=colors[method],
            lw=1.8 if method == "sqlguardllm" else 1,
        )
    axes[0].set(
        xlabel="Template median validation time (ms)",
        ylabel="Empirical cumulative fraction",
        xlim=(0, 1.8),
        ylim=(0, 1.01),
    )
    axes[0].set_title("48 template clusters", loc="left", fontsize=10)
    axes[0].legend(frameon=False, fontsize=7.5, loc="lower right")
    for method in ("parse_only", "sqlguardllm"):
        selected = [r for r in timing["samples"] if r["method"] == method]
        axes[1].scatter(
            [r["ast_nodes"] for r in selected],
            [r["median_ms"] for r in selected],
            s=20,
            alpha=0.65,
            color=colors[method],
            label=LABELS[method],
        )
    axes[1].set(xlabel="Input AST nodes", ylabel="Template median time (ms)")
    axes[1].set_title("Input size and observed cost", loc="left", fontsize=10)
    axes[1].legend(frameon=False, fontsize=8, loc="upper left")
    save(fig, "latency")


def table(name, columns, headings, rows):
    lines = [
        r"\begin{tabular}{" + columns + "}",
        r"\toprule",
        " & ".join(headings) + r" \\",
        r"\midrule",
    ]
    lines += [" & ".join(map(str, row)) + r" \\" for row in rows]
    lines += [r"\bottomrule", r"\end{tabular}"]
    (PAPER / name).write_text("\n".join(lines) + "\n")


def main():
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    records = combined("original.json")
    edges = combined("edges.json")
    fuzz = combined("generated-summary.json")
    timing = read(LOCAL / "timing.json")
    mutations = read(LOCAL / "mutations.json")
    planner = read(NATIVE / "planner.json")
    architecture()
    outcomes(records)
    heatmap(records)
    latency(timing)
    representative = [r for r in records if r["engine"] == "postgres_service"]
    rows = []
    for method in METHODS:
        c = Counter(r["outcome"] for r in representative if r["method"] == method)
        rows.append(
            [
                LABELS[method],
                c["authorized_match"],
                c["blocked"],
                sum(c[x] for x in ("unsafe_admission", "result_mismatch", "row_cap_violation")),
                c["over_rejected"] + c["execution_error"],
            ]
        )
    table(
        "results-table.tex", "lrrrr", ["Configuration", "Match", "Block", "Mismatch", "Other"], rows
    )
    rows = []
    names = {
        "sqlite": "SQLite",
        "duckdb": "DuckDB",
        "postgres_pglite": "PG/WASM",
        "postgres_service": "PG/server",
    }
    for engine in CORE:
        c = Counter(r["outcome"] for r in edges if r["engine"] == engine)
        rows.append(
            [
                names[engine],
                c["authorized_match"],
                c["blocked"],
                c["over_rejected"],
                c["execution_error"],
            ]
        )
    table("edge-table.tex", "lrrrr", ["Runtime", "Match", "Block", "Reject", "Error"], rows)
    f = fuzz[0]
    assert all(
        {k: v for k, v in row.items() if k != "engine"}
        == {k: v for k, v in f.items() if k != "engine"}
        for row in fuzz
    )
    table(
        "fuzz-table.tex",
        "lr",
        ["Outcome per runtime", "Count"],
        [
            ["Authorized match", f["authorized_match"]],
            ["Correct rejection", f["blocked"]],
            ["Allowed query rejected", f["over_rejected"]],
            ["Rewritten SQL execution error", f["execution_error"]],
            ["Observed policy mismatch", f["policy_violations"]],
        ],
    )
    pretty = {
        "remove_row_filters": "Remove row filters",
        "remove_hidden_check": "Remove column checks",
        "remove_star_check": "Remove star check",
        "remove_function_check": "Remove function check",
        "remove_limit": "Remove result cap",
        "skip_cte_sources": "Skip CTE sources",
        "filter_first_occurrence": "Filter first source only",
    }
    rows = []
    for mutant, label in pretty.items():
        rows.append(
            [
                label,
                *[
                    "Yes"
                    if next(r for r in mutations if r["mutant"] == mutant and r["suite"] == suite)[
                        "killed"
                    ]
                    else "No"
                    for suite in ("original_48", "review_edges", "generated")
                ],
            ]
        )
    table("mutation-table.tex", "lccc", ["Deliberate mutation", "48", "Edges", "Gen."], rows)
    table(
        "timing-table.tex",
        "lrr",
        ["Configuration", "Median (ms)", r"95\% cluster CI"],
        [
            [
                LABELS[r["method"]],
                f"{r['median_template_ms']:.3f}",
                f"[{r['ci95_low_ms']:.3f}, {r['ci95_high_ms']:.3f}]",
            ]
            for r in timing["bootstrap"]
        ],
    )
    rows = []
    for name in ("aggregate", "ordered_range", "cte_aggregate"):
        selected = [r for r in planner["summaries"] if r["case"] == name]
        rows.append(
            [name.replace("_", " "), *[f"{r['execution_median_ms']:.3f}" for r in selected]]
        )
    table("planner-table.tex", "lrr", ["Query", "Direct (ms)", "Guard (ms)"], rows)
    full = next(r for r in timing["bootstrap"] if r["method"] == "sqlguardllm")
    parse = next(r for r in timing["bootstrap"] if r["method"] == "parse_only")
    paired = {r["case"]: r["median_ms"] for r in timing["samples"] if r["method"] == "parse_only"}
    marginal = statistics.median(
        r["median_ms"] - paired[r["case"]]
        for r in timing["samples"]
        if r["method"] == "sqlguardllm"
    )
    macros = {
        "FullMedian": f"{full['median_template_ms']:.3f}",
        "FullLow": f"{full['ci95_low_ms']:.3f}",
        "FullHigh": f"{full['ci95_high_ms']:.3f}",
        "ParseMedian": f"{parse['median_template_ms']:.3f}",
        "MarginalMedian": f"{marginal:.3f}",
        "FuzzDraws": str(f["draws"]),
        "FuzzUnique": str(f["unique_sql"]),
        "FuzzRejects": str(f["over_rejected"]),
        "FuzzErrors": str(f["execution_error"]),
        "FuzzUpperPercent": f"{100 * f['conditional_zero_event_upper95']:.4f}",
        "OriginalObservations": str(len(records)),
        "FullObservations": str(sum(r["method"] == "sqlguardllm" for r in records)),
        "EdgeObservations": str(len(edges)),
    }
    (PAPER / "measurements.tex").write_text(
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in macros.items()) + "\n"
    )
    print(
        json.dumps(
            {"core_runtimes": CORE, "original_observations": len(records), "macros": macros},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
