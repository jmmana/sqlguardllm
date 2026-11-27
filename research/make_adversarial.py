"""Tables, macros and the figure of the adversarial evaluation, computed from stored files only.

Inputs (all checked in, no API call, no engine needed):

- ``research/results/fuzz.json``: statistics of one canary-fuzzing run (``tests/fuzz``).
- ``research/redteam/<date>/attacks.jsonl``, ``manifest.json``, ``rescore.json``: the LLM red-team
  dataset of run 1 and its re-scoring with a later guard (``research/redteam/run.py --rescore``).

Outputs in ``research/paper/``: ``adversarial.tex`` (macros), ``canary-table.tex``,
``redteam-table.tex``, ``technique-table.tex`` and ``redteam.pdf``/``.png``.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "research/paper"
FUZZ = ROOT / "research/results/fuzz.json"
RUN = ROOT / "research/redteam/2026-11-27"
INVARIANTS = (
    "foreign_rows",
    "hidden_or_masked",
    "non_interference",
    "max_rows",
    "fail_closed",
    "idempotence",
)
ENGINE_NAMES = {
    "sqlite": "SQLite",
    "duckdb": "DuckDB",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "tsql": "SQL Server",
}


def tex(text: str) -> str:
    return text.replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def table(name: str, columns: str, headings: list[str], rows: list[list]) -> None:
    lines = [r"\begin{tabular}{" + columns + "}", r"\toprule", " & ".join(headings) + r" \\"]
    lines.append(r"\midrule")
    lines += [" & ".join(map(str, row)) + r" \\" for row in rows]
    lines += [r"\bottomrule", r"\end{tabular}"]
    (PAPER / name).write_text("\n".join(lines) + "\n")


def num(value: int) -> str:
    return f"{value:,}".replace(",", "{,}")


def load() -> tuple[dict, dict, list[dict], dict]:
    fuzz = json.loads(FUZZ.read_text())
    manifest = json.loads((RUN / "manifest.json").read_text())
    attacks = [
        json.loads(line) for line in (RUN / "attacks.jsonl").read_text().splitlines() if line
    ]
    rescore = json.loads((RUN / "rescore.json").read_text())
    return fuzz, manifest, attacks, rescore


def summarize_attacks(attacks: list[dict]) -> dict:
    """Every red-team number, recomputed from the per-query records."""
    status = Counter(a["status"] for a in attacks)
    rejected = [a for a in attacks if not a["verdict"]["allowed"]]
    # a rejected query is counted once per distinct rule that rejected it
    by_rule_queries = Counter(
        code for a in rejected for code in {v["code"] for v in a["verdict"]["violations"]}
    )
    by_rule_violations = Counter(v["code"] for a in rejected for v in a["verdict"]["violations"])
    dialect = {
        d: Counter(
            "rejected"
            if not a["verdict"]["allowed"]
            else ("bypass" if a["status"] == "bypass" else "allowed")
            for a in attacks
            if a["dialect"] == d
        )
        for d in sorted({a["dialect"] for a in attacks})
    }
    executed = Counter(
        (engine, role["outcome"])
        for a in attacks
        for engine, per in a["engines"].items()
        for role in (per.values() if "outcome" not in per else [per])
        if isinstance(role, dict) and role["outcome"] == "executed"
    )
    return {
        "total": len(attacks),
        "unique": len({a["fingerprint"] for a in attacks}),
        "duplicates": sum(a["duplicate_of"] is not None for a in attacks),
        "status": status,
        "allowed_by_guard": sum(a["verdict"]["allowed"] for a in attacks),
        "rejected_by_guard": len(rejected),
        "by_rule_queries": by_rule_queries,
        "by_rule_violations": by_rule_violations,
        "by_dialect": dialect,
        "by_technique": Counter(a["technique"] for a in attacks),
        "by_invariant": Counter(a["target_invariant"] for a in attacks),
        "models": Counter(a["model"] for a in attacks),
        "versions": Counter(a["sqlguardllm_version"] for a in attacks),
        "executed_runs": sum(executed.values()),
        "executed_by_engine": Counter(
            {e: sum(c for (eng, _), c in executed.items() if eng == e) for e, _ in executed}
        ),
        "fixed_in": {a["index"]: a["fix_release"] for a in attacks if a["status"] == "bypass"},
        "bypass_sql": {a["index"]: a["sql"] for a in attacks if a["status"] == "bypass"},
    }


def main() -> None:
    fuzz, manifest, attacks, rescore = load()
    s = summarize_attacks(attacks)
    # Consistency of the stored manifest with the per-query records: the paper uses the records.
    assert s["total"] == manifest["generated"] and s["unique"] == manifest["unique"]
    assert s["allowed_by_guard"] == manifest["allowed"] + len(manifest["bypasses"])
    assert s["rejected_by_guard"] == manifest["rejected"]
    assert {i for i in s["fixed_in"]} == {b["index"] for b in manifest["bypasses"]}
    rt = rescore["totals"]
    later = json.loads((RUN / "rescore-0.5.0a7.json").read_text())
    # the later re-scoring (package 0.5.0a7) must agree with the first one
    assert later["totals"] == rt and later["changed_verdicts"] == rescore["changed_verdicts"]
    assert later["bypasses"] == [] and later["invariant_failures"] == []
    assert rt["generated"] == s["total"] and rescore["bypasses"] == []

    engines = fuzz["engines"]
    allowed = sum(e["allowed"] for e in engines.values())
    checked = sum(e["checked"] for e in engines.values())
    rejected = sum(e["rejected"] for e in engines.values())
    executed = sum(e["executed"] for e in engines.values())
    failures = sum(fuzz["invariant_failures"].values())
    ignored = sum(e["engine_ignored_limit"] for e in engines.values())
    sdate = fuzz["timestamp_utc"][:10]

    codes = Counter()
    for e in engines.values():
        codes.update(e["rejected_by_code"])

    macros = {
        "CFExamples": num(fuzz["examples"]),
        "CFSeed": str(fuzz["seed"]),
        "CFProfile": fuzz["profile"],
        "CFDate": sdate,
        "CFEngines": ", ".join(ENGINE_NAMES[n] for n in engines),
        "CFChecked": num(checked),
        "CFAllowed": num(allowed),
        "CFRejected": num(rejected),
        "CFExecuted": num(executed),
        "CFFailures": str(failures),
        "CFIgnoredLimit": num(ignored),
        "CFDuration": f"{fuzz['duration_s']:.0f}",
        "CFSqlglot": str(fuzz["packages"]["sqlglot"]),
        "CFDuckdb": str(fuzz["packages"]["duckdb"]),
        "CFHypothesis": str(fuzz["packages"]["hypothesis"]),
        "CFGuard": str(fuzz["packages"]["sqlguardllm"]),
        "RTDate": manifest["date"],
        "RTModelRequested": "claude-opus-5-5",
        "RTModelAnswered": ", ".join(sorted(s["models"])),
        "RTFallback": "yes" if manifest["fallback_used"] else "no",
        "RTAttacks": str(s["total"]),
        "RTUnique": str(s["unique"]),
        "RTDuplicates": str(s["duplicates"]),
        "RTAllowedGuard": str(s["allowed_by_guard"]),
        "RTAllowedSafe": str(s["status"]["allowed"]),
        "RTRejected": str(s["rejected_by_guard"]),
        "RTBypasses": str(s["status"]["bypass"]),
        "RTTechniques": str(len(s["by_technique"])),
        "RTMaxTechnique": str(max(s["by_technique"].values())),
        "RTCost": f"{manifest['cost_usd']:.2f}",
        "RTCostMicro": str(manifest["cost_microusd"]),
        "RTInTok": num(manifest["usage"]["input_tokens"]),
        "RTOutTok": num(manifest["usage"]["output_tokens"]),
        "RTEstimate": f"{manifest['estimate']['estimated_cost_microusd'] / 1e6:.2f}",
        "RTMaxUsd": f"{manifest['max_usd']:.2f}",
        "RTBudget": num(manifest["token_budget"]),
        "RTMaxAttacks": str(manifest["max_attacks"]),
        "RTStop": manifest["stop_reason"].replace("_", r"\_"),
        "RTMalformed": str(manifest["malformed_items"]),
        "RTExecutedRuns": str(s["executed_runs"]),
        "RTGuardVersion": manifest["sqlguardllm_version"],
        "RTPromptHash": manifest["prompt_sha256"][:12],
        "RTPolicyHash": manifest["policy_sha256"][:12],
        "RTRescoreVersion": rescore["sqlguardllm_version"],
        "RTRescoreSha": rescore["git_sha"][:7],
        "RTRescoreLater": later["sqlguardllm_version"],
        "RTRescoreRejected": str(rt["rejected"]),
        "RTRescoreAllowed": str(rt["allowed"]),
        "RTRescoreBypasses": str(len(rescore["bypasses"])),
        "RTRescoreChanged": str(len(rescore["changed_verdicts"])),
        "RTRescoreEngines": ", ".join(ENGINE_NAMES[e] for e in rescore["engines_executed"]),
        "RTBypassFix": next(iter(s["fixed_in"].values())),
    }
    (PAPER / "adversarial.tex").write_text(
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in macros.items()) + "\n"
    )

    # Table: canary fuzzing, per engine.
    rows = []
    for name, e in engines.items():
        rows.append(
            [
                ENGINE_NAMES[name],
                num(e["checked"]),
                num(e["allowed"]),
                num(e["rejected"]),
                num(e["executed"]),
                num(e["engine_errors"]),
                num(e["engine_ignored_limit"]),
                sum(e["invariant_failures"].values()),
            ]
        )
    table(
        "canary-table.tex",
        "lrrrrrrr",
        [
            "Engine",
            "Checked",
            "Allowed",
            "Rejected",
            "Executed",
            "Eng.\\ err.",
            "Ign.\\ LIMIT",
            "Fail.",
        ],
        rows,
    )

    # Table: red team by guard verdict and by rule.
    verdict_rows = [
        ["Generated (kept)", s["total"]],
        ["Rejected by the guard", s["rejected_by_guard"]],
        ["Allowed, canary oracle clean", s["status"]["allowed"]],
        ["Allowed, canary in output (bypass)", s["status"]["bypass"]],
        ["Duplicates (same normalised SQL)", s["duplicates"]],
        ["Rejected by rule (queries):", ""],
    ]
    verdict_rows += [[r"\quad " + tex(code), n] for code, n in s["by_rule_queries"].most_common()]
    table("redteam-table.tex", "lr", ["Red-team run 1", "Queries"], verdict_rows)

    # Table: techniques, one row each, queries and verdict.
    trows = []
    for tech, n in s["by_technique"].most_common():
        verdicts = Counter(
            "bypass"
            if a["status"] == "bypass"
            else ("allowed" if a["verdict"]["allowed"] else "rejected")
            for a in attacks
            if a["technique"] == tech
        )
        trows.append([tex(tech), n, verdicts["rejected"], verdicts["allowed"], verdicts["bypass"]])
    table(
        "technique-table.tex",
        "lrrrr",
        ["Technique (as named by the model)", "Q", "Rej.", "Allow.", "Byp."],
        trows,
    )

    # Figure: verdict per dialect and rejections by rule.
    fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [1, 1.1]})
    dialects = sorted(s["by_dialect"], key=lambda d: -sum(s["by_dialect"][d].values()))
    left = [0] * len(dialects)
    colours = {"rejected": "#0072B2", "allowed": "#999999", "bypass": "#D55E00"}
    labels = {"rejected": "rejected", "allowed": "allowed, safe", "bypass": "bypass"}
    for kind in ("rejected", "allowed", "bypass"):
        vals = [s["by_dialect"][d][kind] for d in dialects]
        ax[0].barh(
            [ENGINE_NAMES.get(d, d) for d in dialects],
            vals,
            left=left,
            color=colours[kind],
            label=labels[kind],
        )
        left = [a + b for a, b in zip(left, vals, strict=True)]
    ax[0].invert_yaxis()
    ax[0].set(xlabel="Queries")
    ax[0].set_title("Guard verdict by dialect", loc="left", fontsize=9)
    ax[0].legend(frameon=False, fontsize=7, loc="lower right")
    rules = s["by_rule_queries"].most_common()
    ax[1].barh([r for r, _ in rules], [n for _, n in rules], color="#0072B2")
    ax[1].invert_yaxis()
    ax[1].set(xlabel="Rejected queries")
    ax[1].set_title("Rule that rejected them", loc="left", fontsize=9)
    for i, (_, n) in enumerate(rules):
        ax[1].text(n + 0.2, i, str(n), va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(PAPER / "redteam.pdf", bbox_inches="tight", metadata={"CreationDate": None})
    fig.savefig(PAPER / "redteam.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(json.dumps(macros, indent=2))


if __name__ == "__main__":
    main()
