"""Synthetic, versioned SQL workloads; no customer data or model calls.

The execution workload was authored for this study. It is separate from the
project's development regression corpus, but is not an independently collected
or externally held-out benchmark. Every query and intended outcome is public.
"""

# SQL literals stay on one line so the published templates are easy to audit.
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    sql: str

    @property
    def expected(self) -> str:
        return "reject" if self.kind == "forbidden" else "allow"


BENIGN = (
    ("literal", "SELECT 1 AS n"),
    ("keyword_literal", "SELECT 'DELETE DROP UPDATE' AS note"),
    ("keyword_comment", "SELECT 1 /* DELETE FROM invoices */ AS n"),
    ("semicolon_literal", "SELECT 'x; DROP TABLE invoices' AS note"),
    ("keyword_identifier", "SELECT 1 AS delete_count"),
    ("projection", "SELECT id, total FROM invoices ORDER BY id"),
    ("public_columns", "SELECT id, name FROM customers ORDER BY id"),
    ("branches", "SELECT id, name FROM branches ORDER BY id"),
    ("predicate", "SELECT id, total FROM invoices WHERE total > 103 ORDER BY id"),
    (
        "join",
        "SELECT i.id, c.name, i.total FROM invoices i JOIN customers c ON c.id = i.customer_id ORDER BY i.id",
    ),
    (
        "left_join",
        "SELECT i.id, b.name FROM invoices i LEFT JOIN branches b ON b.id = i.branch_id ORDER BY i.id",
    ),
    ("count", "SELECT COUNT(*) AS n FROM invoices"),
    ("sum", "SELECT SUM(total) AS total FROM invoices"),
    (
        "group",
        "SELECT branch_id, SUM(total) AS total FROM invoices GROUP BY branch_id ORDER BY branch_id",
    ),
    (
        "having",
        "SELECT branch_id, COUNT(*) AS n FROM invoices GROUP BY branch_id HAVING COUNT(*) > 1 ORDER BY branch_id",
    ),
    ("distinct", "SELECT DISTINCT category FROM invoices ORDER BY category"),
    (
        "case",
        "SELECT id, CASE WHEN total > 103 THEN 'large' ELSE 'small' END AS size FROM invoices ORDER BY id",
    ),
    (
        "cte",
        "WITH report AS (SELECT branch_id, SUM(total) AS total FROM invoices GROUP BY branch_id) SELECT * FROM report ORDER BY branch_id",
    ),
    ("derived", "SELECT q.id, q.total FROM (SELECT id, total FROM invoices) q ORDER BY q.id"),
    (
        "correlated",
        "SELECT c.id, (SELECT SUM(i.total) FROM invoices i WHERE i.customer_id = c.id) AS total FROM customers c ORDER BY c.id",
    ),
    (
        "exists",
        "SELECT c.id, c.name FROM customers c WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = c.id) ORDER BY c.id",
    ),
    ("window", "SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS position FROM invoices ORDER BY id"),
    (
        "union",
        "SELECT id FROM invoices WHERE id % 2 = 0 UNION SELECT id FROM invoices WHERE id % 2 = 1 ORDER BY id",
    ),
    ("existing_limit", "SELECT id, total FROM invoices ORDER BY id LIMIT 3"),
)

ISOLATION = (
    (
        "tautology",
        "SELECT id, tenant_id, total FROM invoices WHERE tenant_id = 999 OR 1 = 1 ORDER BY id",
    ),
    ("other_tenant", "SELECT id, total FROM invoices WHERE tenant_id = {other} ORDER BY id"),
    ("negated_tenant", "SELECT id, total FROM invoices WHERE NOT tenant_id = {tenant} ORDER BY id"),
    (
        "cte_all_rows",
        "WITH report AS (SELECT id, tenant_id FROM invoices) SELECT * FROM report ORDER BY id",
    ),
    (
        "union_branches",
        "SELECT id, tenant_id FROM invoices WHERE tenant_id = 1 UNION ALL SELECT id, tenant_id FROM invoices WHERE tenant_id = 2 ORDER BY id",
    ),
    ("scalar_count", "SELECT (SELECT COUNT(*) FROM invoices) AS n"),
    (
        "correlated_other",
        "SELECT c.id FROM customers c WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = c.id AND i.tenant_id = {other}) ORDER BY c.id",
    ),
    (
        "self_join",
        "SELECT a.id, a.tenant_id, b.tenant_id FROM invoices a JOIN invoices b ON a.id = b.id ORDER BY a.id",
    ),
    (
        "join_tautology",
        "SELECT i.id, c.id AS customer FROM invoices i JOIN customers c ON i.customer_id = c.id OR 1 = 1 ORDER BY i.id, c.id",
    ),
    ("aggregate", "SELECT SUM(total) AS total, COUNT(*) AS n FROM invoices"),
    ("oversized_limit", "SELECT id, tenant_id FROM invoices ORDER BY id LIMIT 10000"),
    (
        "nested_cte",
        "WITH a AS (SELECT id FROM invoices), b AS (SELECT id FROM a UNION ALL SELECT id FROM invoices) SELECT COUNT(*) AS n FROM b",
    ),
)

FORBIDDEN = (
    ("hidden_projection", "SELECT email FROM customers ORDER BY id"),
    ("hidden_predicate", "SELECT id FROM customers WHERE email > 'a' ORDER BY id"),
    ("hidden_order", "SELECT id FROM customers ORDER BY LOWER(email)"),
    ("hidden_group", "SELECT email, COUNT(*) FROM customers GROUP BY email ORDER BY email"),
    ("hidden_having", "SELECT COUNT(*) FROM customers HAVING MAX(email) > 'a'"),
    (
        "hidden_derived",
        "SELECT q.contact FROM (SELECT email AS contact FROM customers) q ORDER BY q.contact",
    ),
    (
        "hidden_cte",
        "WITH c AS (SELECT email AS contact FROM customers) SELECT contact FROM c ORDER BY contact",
    ),
    ("forbidden_union", "SELECT id FROM invoices UNION SELECT id FROM secrets ORDER BY id"),
    ("hidden_star", "SELECT * FROM customers ORDER BY id"),
    ("forbidden_table", "SELECT token FROM secrets ORDER BY id"),
    ("forbidden_scalar", "SELECT (SELECT MAX(token) FROM secrets) AS value"),
    ("forbidden_cte", "WITH s AS (SELECT token FROM secrets) SELECT token FROM s"),
)


def cases(tenant: int) -> list[Case]:
    return [
        Case(f"{kind}_{name}", kind, sql.format(tenant=tenant, other=3 - tenant))
        for kind, entries in (
            ("benign", BENIGN),
            ("isolation", ISOLATION),
            ("forbidden", FORBIDDEN),
        )
        for name, sql in entries
    ]


def manifest() -> list[dict]:
    return [asdict(case) | {"expected": case.expected} for case in cases(1)]


def fixture_sql(tenant: int | None = None, *, oracle: bool = False) -> str:
    """The reference physically omits private columns/tables and other tenants.

    This creates an execution oracle without using sqlguardllm or rewriting the
    query under test. Query results are compared as bags after an eight-row cap.
    """
    email = "" if oracle else ", email TEXT"
    sql = [
        f"CREATE TABLE customers (id INTEGER PRIMARY KEY, tenant_id INTEGER, name TEXT{email})",
        "CREATE TABLE branches (id INTEGER PRIMARY KEY, tenant_id INTEGER, name TEXT)",
        "CREATE TABLE invoices (id INTEGER PRIMARY KEY, tenant_id INTEGER, customer_id INTEGER, branch_id INTEGER, total INTEGER, category TEXT)",
    ]
    for t in (1, 2):
        if tenant is not None and t != tenant:
            continue
        for j in range(1, 5):
            vals = f"{t * 100 + j}, {t}, 'Customer {t}-{j}'"
            if not oracle:
                vals += f", 'private-{t}-{j}@example.invalid'"
            sql.append(f"INSERT INTO customers VALUES ({vals})")
        for j in (1, 2):
            sql.append(f"INSERT INTO branches VALUES ({t * 10 + j}, {t}, 'Branch {t}-{j}')")
        for j in range(1, 7):
            sql.append(
                f"INSERT INTO invoices VALUES ({t * 1000 + j}, {t}, "
                f"{t * 100 + (j - 1) % 4 + 1}, {t * 10 + j % 2 + 1}, "
                f"{t * 100 + j}, '{'A' if j % 2 else 'B'}')"
            )
    if not oracle:
        sql.extend(
            (
                "CREATE TABLE secrets (id INTEGER PRIMARY KEY, token TEXT)",
                "INSERT INTO secrets VALUES (9999, 'synthetic-secret')",
            )
        )
    return ";\n".join(sql) + ";\n"
