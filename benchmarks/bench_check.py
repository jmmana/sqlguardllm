"""Performance budget of ``Guard.check()``: median under 2 ms per query (docs/DESIGN.md).

Times 21 representative queries, from a one-table select to a large report, on a warm
``Guard`` (policy compiled once, as in an application). Every call is timed on its own with
``time.perf_counter_ns``; the cases are interleaved in rounds so a noisy moment on a shared
machine slows all of them alike instead of one. The garbage collector stays on, as in
production. Reports the median and the 95th percentile per query.

Usage::

    uv run python benchmarks/bench_check.py                    # table; exit 1 over budget
    uv run python benchmarks/bench_check.py --json head.json   # also save the numbers
    uv run python benchmarks/bench_check.py compare --base main.json --head head.json

``compare`` exits 1 when a query's median is more than 25 % slower than on the base. Given
several files per side it keeps the fastest median of each query, because noise on a shared
machine only ever adds time.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import sqlguardllm
from sqlguardllm import Guard, Policy

BUDGET_MS = 2.0
# The large report (5 CTEs, 10 joins, 7 filtered table occurrences) is outside the "one to
# five joins" the budget is about. It is reported, compared against the base branch like the
# others, and has a ceiling of its own so a blow-up still fails.
LARGE_FACTOR = 5.0
REGRESSION_THRESHOLD = 0.25

TABLES = {"invoices", "invoice_lines", "branches", "customers", "products", "payments"}
HIDDEN = {"customers.email", "customers.tax_id"}
MASKED = {"customers.phone": "partial"}
FILTERS = {"invoices": "branch_id IN ({user.branches})", "branches": "id IN ({user.branches})"}
CONTEXT: dict[str, Any] = {"user": {"branches": [1, 2, 3]}}


def policies() -> dict[str, Policy]:
    """The policies the cases run under: the usual one, and one with every optional limit."""
    return {
        "standard": Policy(
            dialect="postgres",
            tables=TABLES,
            hidden_columns=HIDDEN,
            masked_columns=MASKED,
            row_filters=FILTERS,
            max_rows=1000,
        ),
        "strict": Policy(
            dialect="postgres",
            tables=TABLES,
            hidden_columns=HIDDEN,
            masked_columns=MASKED,
            row_filters=FILTERS,
            max_rows=1000,
            max_joins=6,
            max_subquery_depth=3,
            forbid_cartesian=True,
            required_filters={"invoices": ["issued_at"]},
        ),
    }


@dataclass(frozen=True)
class Case:
    """One benchmark query.

    Attributes:
        name: Stable identifier, used to compare runs.
        kind: Group shown in the report (simple, filter, join, cte, subquery, set, rejected,
            large).
        sql: The query, as a model would write it.
        policy: Key in :func:`policies`.
        allowed: Whether the guard must accept it (checked by the tests, not timed).
        budget_factor: Multiple of the budget this case gets. 1 for every typical query (one
            to five joins, the scope of the budget in docs/DESIGN.md); only the large report
            has a ceiling of its own, so it is still caught if it blows up.
    """

    name: str
    kind: str
    sql: str
    policy: str = "standard"
    allowed: bool = True
    budget_factor: float = 1.0


LARGE_REPORT = """
WITH branch_sales AS (
    SELECT i.branch_id, date_trunc('month', i.issued_at) AS month,
           SUM(l.quantity * l.unit_price) AS gross, SUM(l.discount) AS discounts,
           COUNT(DISTINCT i.id) AS invoice_count, COUNT(DISTINCT i.customer_id) AS buyers
    FROM invoices AS i
    JOIN invoice_lines AS l ON l.invoice_id = i.id
    WHERE i.issued_at >= DATE '2026-01-01' AND i.status IN ('paid', 'sent')
    GROUP BY i.branch_id, date_trunc('month', i.issued_at)
),
category_sales AS (
    SELECT p.category, i.branch_id, SUM(l.quantity) AS units,
           SUM(l.quantity * l.unit_price) AS revenue
    FROM invoice_lines AS l
    JOIN products AS p ON p.id = l.product_id
    JOIN invoices AS i ON i.id = l.invoice_id
    WHERE i.issued_at >= DATE '2026-01-01'
    GROUP BY p.category, i.branch_id
),
top_categories AS (
    SELECT branch_id, category, revenue,
           RANK() OVER (PARTITION BY branch_id ORDER BY revenue DESC) AS rnk
    FROM category_sales
),
payments_by_branch AS (
    SELECT i.branch_id, SUM(pm.amount) AS paid, MAX(pm.paid_at) AS last_payment
    FROM payments AS pm
    JOIN invoices AS i ON i.id = pm.invoice_id
    GROUP BY i.branch_id
),
customer_mix AS (
    SELECT i.branch_id,
           COUNT(DISTINCT CASE WHEN c.segment = 'enterprise' THEN c.id END) AS enterprise,
           COUNT(DISTINCT CASE WHEN c.segment = 'smb' THEN c.id END) AS smb
    FROM invoices AS i
    JOIN customers AS c ON c.id = i.customer_id
    GROUP BY i.branch_id
)
SELECT b.name AS branch, b.region, s.month, s.gross, s.discounts,
       s.gross - s.discounts AS net, s.invoice_count, s.buyers,
       ROUND((s.gross - s.discounts) / NULLIF(s.invoice_count, 0), 2) AS avg_ticket,
       LAG(s.gross) OVER (PARTITION BY s.branch_id ORDER BY s.month) AS previous_gross,
       SUM(s.gross) OVER (
           PARTITION BY s.branch_id ORDER BY s.month
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS running_gross,
       t.category AS top_category, t.revenue AS top_category_revenue,
       pb.paid, pb.last_payment, cm.enterprise, cm.smb,
       CASE WHEN pb.paid >= s.gross THEN 'settled'
            WHEN pb.paid >= s.gross * 0.8 THEN 'mostly paid'
            ELSE 'pending' END AS collection_status,
       COALESCE((SELECT MAX(i2.total) FROM invoices AS i2
                 WHERE i2.branch_id = b.id AND i2.issued_at >= s.month), 0) AS largest_invoice
FROM branch_sales AS s
JOIN branches AS b ON b.id = s.branch_id
LEFT JOIN top_categories AS t ON t.branch_id = s.branch_id AND t.rnk = 1
LEFT JOIN payments_by_branch AS pb ON pb.branch_id = s.branch_id
LEFT JOIN customer_mix AS cm ON cm.branch_id = s.branch_id
WHERE b.active
  AND b.id IN (SELECT branch_id FROM invoices WHERE total > 0
               GROUP BY branch_id HAVING COUNT(*) > 10)
ORDER BY b.name, s.month
"""

CASES: tuple[Case, ...] = (
    Case("select_one_table", "simple", "SELECT id, name, price FROM products"),
    Case(
        "select_where_order",
        "simple",
        "SELECT id, name, price FROM products WHERE price > 10 AND category = 'tools' "
        "ORDER BY price DESC LIMIT 20",
    ),
    Case(
        "group_by_having",
        "simple",
        "SELECT category, COUNT(*) AS n, AVG(price) AS avg_price FROM products "
        "GROUP BY category HAVING COUNT(*) > 5 ORDER BY n DESC",
    ),
    Case(
        "masked_column",
        "simple",
        "SELECT id, name, phone FROM customers WHERE city = 'Bogota' ORDER BY name",
    ),
    Case(
        "row_filter_one_table",
        "filter",
        "SELECT id, total FROM invoices WHERE issued_at >= DATE '2026-01-01'",
    ),
    Case(
        "row_filter_aggregate",
        "filter",
        "SELECT branch_id, date_trunc('month', issued_at) AS month, SUM(total) AS revenue "
        "FROM invoices GROUP BY 1, 2 ORDER BY 1, 2",
    ),
    Case(
        "join_2_tables",
        "join",
        "SELECT b.name, SUM(i.total) AS revenue FROM invoices i "
        "JOIN branches b ON b.id = i.branch_id GROUP BY b.name",
    ),
    Case(
        "join_3_tables",
        "join",
        "SELECT p.category, SUM(l.quantity * l.unit_price) AS revenue FROM invoice_lines l "
        "JOIN invoices i ON i.id = l.invoice_id JOIN products p ON p.id = l.product_id "
        "WHERE i.issued_at >= DATE '2026-01-01' GROUP BY p.category ORDER BY revenue DESC",
    ),
    Case(
        "join_5_tables",
        "join",
        "SELECT b.name AS branch, c.name AS customer, p.category, "
        "SUM(l.quantity) AS units, SUM(pm.amount) AS paid "
        "FROM invoices i JOIN branches b ON b.id = i.branch_id "
        "JOIN customers c ON c.id = i.customer_id "
        "JOIN invoice_lines l ON l.invoice_id = i.id "
        "JOIN products p ON p.id = l.product_id "
        "LEFT JOIN payments pm ON pm.invoice_id = i.id "
        "WHERE i.issued_at BETWEEN DATE '2026-01-01' AND DATE '2026-06-30' "
        "GROUP BY b.name, c.name, p.category ORDER BY units DESC",
    ),
    Case(
        "join_5_tables_strict",
        "join",
        "SELECT b.name AS branch, c.name AS customer, p.category, "
        "SUM(l.quantity) AS units, SUM(pm.amount) AS paid "
        "FROM invoices i JOIN branches b ON b.id = i.branch_id "
        "JOIN customers c ON c.id = i.customer_id "
        "JOIN invoice_lines l ON l.invoice_id = i.id "
        "JOIN products p ON p.id = l.product_id "
        "LEFT JOIN payments pm ON pm.invoice_id = i.id "
        "WHERE i.issued_at BETWEEN DATE '2026-01-01' AND DATE '2026-06-30' "
        "GROUP BY b.name, c.name, p.category ORDER BY units DESC",
        policy="strict",
    ),
    Case(
        "comma_join_where",
        "join",
        "SELECT c.name, i.total FROM customers c, invoices i "
        "WHERE i.customer_id = c.id AND i.total > 1000",
    ),
    Case(
        "cte_one",
        "cte",
        "WITH recent AS (SELECT * FROM invoices WHERE issued_at >= DATE '2026-11-01') "
        "SELECT branch_id, COUNT(*) AS n, SUM(total) AS revenue FROM recent GROUP BY branch_id",
    ),
    Case(
        "cte_chain",
        "cte",
        "WITH monthly AS (SELECT branch_id, date_trunc('month', issued_at) AS month, "
        "SUM(total) AS revenue FROM invoices GROUP BY 1, 2), "
        "ranked AS (SELECT branch_id, month, revenue, "
        "RANK() OVER (PARTITION BY month ORDER BY revenue DESC) AS rnk FROM monthly), "
        "best AS (SELECT * FROM ranked WHERE rnk <= 3) "
        "SELECT b.name, best.month, best.revenue FROM best "
        "JOIN branches b ON b.id = best.branch_id ORDER BY best.month, best.rnk",
    ),
    Case(
        "subquery_in",
        "subquery",
        "SELECT name, city FROM customers WHERE id IN "
        "(SELECT customer_id FROM invoices WHERE total > 500)",
    ),
    Case(
        "subquery_exists_correlated",
        "subquery",
        "SELECT p.id, p.name FROM products p WHERE NOT EXISTS "
        "(SELECT 1 FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "WHERE l.product_id = p.id AND i.issued_at >= DATE '2026-01-01')",
    ),
    Case(
        "derived_tables",
        "subquery",
        "SELECT t.branch_id, t.revenue, t.revenue / s.total AS share FROM "
        "(SELECT branch_id, SUM(total) AS revenue FROM invoices GROUP BY branch_id) t "
        "CROSS JOIN (SELECT SUM(total) AS total FROM invoices) s ORDER BY share DESC",
    ),
    Case(
        "scalar_subqueries",
        "subquery",
        "SELECT b.name, (SELECT COUNT(*) FROM invoices i WHERE i.branch_id = b.id) AS n, "
        "(SELECT MAX(total) FROM invoices i WHERE i.branch_id = b.id) AS largest "
        "FROM branches b",
    ),
    Case(
        "union_window",
        "set",
        "SELECT 'invoice' AS kind, id, total, "
        "ROW_NUMBER() OVER (ORDER BY total DESC) AS pos FROM invoices "
        "UNION ALL SELECT 'payment', id, amount, "
        "ROW_NUMBER() OVER (ORDER BY amount DESC) FROM payments",
    ),
    Case(
        "rejected_hidden_column",
        "rejected",
        "SELECT c.name, c.email, SUM(i.total) FROM customers c "
        "JOIN invoices i ON i.customer_id = c.id GROUP BY c.name, c.email",
        allowed=False,
    ),
    Case(
        "rejected_write_in_cte",
        "rejected",
        "WITH d AS (DELETE FROM invoices WHERE total < 0 RETURNING *) SELECT COUNT(*) FROM d",
        allowed=False,
    ),
    Case("large_report", "large", LARGE_REPORT, budget_factor=LARGE_FACTOR),
)


@dataclass(frozen=True)
class Timing:
    """Timings of one case, in milliseconds."""

    name: str
    kind: str
    median_ms: float
    p95_ms: float
    min_ms: float
    samples: int
    budget_ms: float

    @property
    def over_budget(self) -> bool:
        return self.median_ms > self.budget_ms


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """The ``q`` quantile (0-1) of already sorted values, linearly interpolated."""
    if not sorted_values:
        raise ValueError("no values")
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def measure(
    cases: Sequence[Case] = CASES,
    *,
    rounds: int = 20,
    per_round: int = 25,
    warmup: int = 20,
    budget_ms: float = BUDGET_MS,
    context: Mapping[str, Any] = CONTEXT,
) -> list[Timing]:
    """Time ``Guard.check()`` on every case: ``rounds * per_round`` calls each, interleaved."""
    built = policies()
    guards = {name: Guard(policy) for name, policy in built.items()}
    samples: dict[str, list[int]] = {case.name: [] for case in cases}
    for case in cases:
        check = guards[case.policy].check
        for _ in range(warmup):
            check(case.sql, context)
    clock = time.perf_counter_ns
    for _ in range(rounds):
        for case in cases:
            check = guards[case.policy].check
            bucket = samples[case.name]
            for _ in range(per_round):
                start = clock()
                check(case.sql, context)
                bucket.append(clock() - start)
    timings = []
    for case in cases:
        values = sorted(ns / 1e6 for ns in samples[case.name])
        timings.append(
            Timing(
                name=case.name,
                kind=case.kind,
                median_ms=statistics.median(values),
                p95_ms=percentile(values, 0.95),
                min_ms=values[0],
                samples=len(values),
                budget_ms=budget_ms * case.budget_factor,
            )
        )
    return timings


def cpu_model() -> str:
    """The CPU model, to tell runners apart in reports."""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "system": platform.system(),
        "cpu": cpu_model(),
        "sqlglot": version("sqlglot"),
        "sqlguardllm": sqlguardllm.__version__,
    }


def render(timings: Sequence[Timing]) -> str:
    """A Markdown table, readable in a terminal and in a GitHub job summary."""
    lines = [
        "| Query | Kind | Median (ms) | p95 (ms) | Budget (ms) | |",
        "|---|---|--:|--:|--:|:-:|",
    ]
    for t in timings:
        status = "**over**" if t.over_budget else "ok"
        lines.append(
            f"| `{t.name}` | {t.kind} | {t.median_ms:.3f} | {t.p95_ms:.3f} "
            f"| {t.budget_ms:g} | {status} |"
        )
    medians = [t.median_ms for t in timings]
    lines.append(
        f"| **all {len(timings)}** | | {statistics.median(medians):.3f} (median of medians) "
        f"| {max(t.p95_ms for t in timings):.3f} (worst) | | |"
    )
    return "\n".join(lines)


def load_medians(paths: Sequence[Path]) -> dict[str, float]:
    """Fastest median per case across result files (noise on a shared machine only adds)."""
    best: dict[str, float] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data["timings"]:
            name, value = str(row["name"]), float(row["median_ms"])
            best[name] = min(value, best.get(name, value))
    return best


def regressions(
    base: Mapping[str, float], head: Mapping[str, float], threshold: float
) -> list[tuple[str, float, float, float]]:
    """Cases present in both whose head median exceeds the base one by more than ``threshold``."""
    slower = []
    for name in sorted(base.keys() & head.keys()):
        ratio = head[name] / base[name]
        if ratio > 1 + threshold:
            slower.append((name, base[name], head[name], ratio))
    return slower


def render_comparison(base: Mapping[str, float], head: Mapping[str, float]) -> str:
    lines = ["| Query | Base (ms) | Head (ms) | Change |", "|---|--:|--:|--:|"]
    for name in head:
        if name in base:
            change = (head[name] / base[name] - 1) * 100
            lines.append(f"| `{name}` | {base[name]:.3f} | {head[name]:.3f} | {change:+.1f} % |")
        else:
            lines.append(f"| `{name}` | new | {head[name]:.3f} | |")
    return "\n".join(lines)


def write_summary(path: str | None, text: str) -> None:
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n\n")


def run_command(args: argparse.Namespace) -> int:
    timings = measure(
        rounds=args.rounds,
        per_round=args.per_round,
        warmup=args.warmup,
        budget_ms=args.budget_ms,
    )
    env = environment()
    table = render(timings)
    header = (
        f"Guard.check() on {len(timings)} queries, {timings[0].samples} calls each: "
        f"Python {env['python']}, sqlglot {env['sqlglot']}, sqlguardllm {env['sqlguardllm']}, "
        f"{env['system']} {env['machine']} ({env['cpu']})"
    )
    print(header)
    print(table)
    write_summary(args.summary, f"### Performance budget\n\n{header}\n\n{table}")
    if args.json:
        payload = {
            "budget_ms": args.budget_ms,
            "environment": env,
            "timings": [asdict(t) for t in timings],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    slow = [t for t in timings if t.over_budget]
    if slow and not args.no_budget:
        names = ", ".join(f"{t.name} ({t.median_ms:.3f} > {t.budget_ms:g} ms)" for t in slow)
        print(f"\nOver budget: {names}", file=sys.stderr)
        return 1
    return 0


def compare_command(args: argparse.Namespace) -> int:
    base = load_medians(args.base)
    head = load_medians(args.head)
    table = render_comparison(base, head)
    print(table)
    write_summary(args.summary, f"### Against the base branch\n\n{table}")
    slower = regressions(base, head, args.threshold)
    if slower:
        for name, before, after, ratio in slower:
            print(
                f"{name}: {before:.3f} ms -> {after:.3f} ms ({(ratio - 1) * 100:+.1f} %)",
                file=sys.stderr,
            )
        print(f"\nRegression over {args.threshold:.0%} against the base branch.", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--rounds", type=int, default=20, help="interleaved rounds (default 20)")
    parser.add_argument("--per-round", type=int, default=25, help="calls per case and round")
    parser.add_argument("--warmup", type=int, default=20, help="untimed calls per case first")
    parser.add_argument("--budget-ms", type=float, default=BUDGET_MS)
    parser.add_argument("--no-budget", action="store_true", help="report only, never fail")
    parser.add_argument("--json", help="write the timings to this file")
    parser.add_argument("--summary", help="append a Markdown report to this file")
    sub = parser.add_subparsers(dest="command")
    compare = sub.add_parser("compare", help="compare saved runs of two branches")
    compare.add_argument("--base", type=Path, nargs="+", required=True)
    compare.add_argument("--head", type=Path, nargs="+", required=True)
    compare.add_argument("--threshold", type=float, default=REGRESSION_THRESHOLD)
    compare.add_argument("--summary", help="append a Markdown report to this file")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "compare":
        return compare_command(args)
    return run_command(args)


if __name__ == "__main__":
    sys.exit(main())
