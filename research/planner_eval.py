"""Paired EXPLAIN ANALYZE observations on a larger synthetic native PG fixture."""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from pathlib import Path

from native_postgres import NativeSession

from sqlguardllm import Guard, Policy

ROOT = Path(__file__).resolve().parents[1]


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


def measure(session, query):
    output = session.command(
        "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + query.strip().rstrip(";")
    )
    match = re.search(r'QUERY PLAN = "(.*?)"\s+\(typeid', output, re.S)
    if match is None:
        raise RuntimeError("Native EXPLAIN did not return a JSON plan")
    return json.loads(match.group(1))[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--out", type=Path, default=ROOT / "research/results-review/planner.json")
    args = parser.parse_args()
    session = NativeSession(
        "CREATE TABLE invoices (id bigint, tenant_id integer, total integer);"
        f"INSERT INTO invoices SELECT g, (g%50)+1, (g*13)%1000 FROM generate_series(1,{args.rows}) g;"
        "CREATE INDEX invoices_tenant_id ON invoices(tenant_id,id) INCLUDE(total);ANALYZE invoices;"
    )
    guard = Guard(
        Policy(
            dialect="postgres",
            tables={"invoices"},
            max_rows=100,
            row_filters={"invoices": "tenant_id = {user.tenant_id}"},
        )
    )
    queries = {
        "aggregate": (
            "SELECT COUNT(*), SUM(total) FROM invoices",
            "SELECT COUNT(*), SUM(total) FROM invoices WHERE tenant_id=7 LIMIT 100",
        ),
        "ordered_range": (
            "SELECT id, total FROM invoices WHERE id BETWEEN 100000 AND 400000 ORDER BY id LIMIT 50",
            "SELECT id, total FROM invoices WHERE tenant_id=7 AND id BETWEEN 100000 AND 400000 ORDER BY id LIMIT 50",
        ),
        "cte_aggregate": (
            "WITH r AS (SELECT total FROM invoices WHERE total>700) SELECT COUNT(*), SUM(total) FROM r",
            "WITH r AS (SELECT total FROM invoices WHERE tenant_id=7 AND total>700) SELECT COUNT(*), SUM(total) FROM r LIMIT 100",
        ),
    }
    rng = random.Random(2026112604)
    records, summaries = [], []
    try:
        session.command("VACUUM (ANALYZE) invoices;", readonly=False)
        version = session.query("SELECT version()", 1)[0][0]
        for name, (source, direct) in queries.items():
            rewritten = guard.enforce(source, {"user": {"tenant_id": 7}})
            assert session.query(direct) == session.query(rewritten), name
            variants = {"direct_predicate": direct, "derived_guard": rewritten}
            for q in variants.values():
                for _ in range(3):
                    measure(session, q)
            for round_ in range(args.rounds):
                order = list(variants)
                rng.shuffle(order)
                for variant in order:
                    plan = measure(session, variants[variant])
                    records.append(
                        {"case": name, "variant": variant, "round": round_, "plan": plan}
                    )
            for variant, q in variants.items():
                selected = [r for r in records if r["case"] == name and r["variant"] == variant]
                first = selected[0]["plan"]["Plan"]
                summaries.append(
                    {
                        "case": name,
                        "variant": variant,
                        "sql": q,
                        "execution_median_ms": statistics.median(
                            r["plan"]["Execution Time"] for r in selected
                        ),
                        "planning_median_ms": statistics.median(
                            r["plan"]["Planning Time"] for r in selected
                        ),
                        "node_types": [n["Node Type"] for n in nodes(first)],
                        "index_conditions": [
                            n["Index Cond"] for n in nodes(first) if "Index Cond" in n
                        ],
                        "filters": [n["Filter"] for n in nodes(first) if "Filter" in n],
                        "subquery_scan": any(
                            n["Node Type"] == "Subquery Scan" for n in nodes(first)
                        ),
                    }
                )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "version": version,
                    "rows": args.rows,
                    "tenants": 50,
                    "rounds": args.rounds,
                    "seed": 2026112604,
                    "warmup_per_variant": 3,
                    "summaries": summaries,
                    "scope": "single-user native planner and warm EXPLAIN timings; synthetic indexed schema, not production overhead",
                    "observations": records,
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(summaries, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
