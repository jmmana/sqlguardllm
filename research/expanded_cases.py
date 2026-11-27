"""Review-driven probes and an explicitly defined probabilistic SQL grammar.

The grammar is authored, not independent of the system's authors. IID-style
bounds, if reported, concern draws from this fixed generator only. Sampling is
with replacement; duplicates are retained and their number is reported.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from workload import cases, fixture_sql


@dataclass(frozen=True)
class Probe:
    id: str
    kind: str
    sql: str
    expected: str = "allow"
    execute: bool = True
    family: str = ""
    allow_view: bool = False


def fixtures(*, null_extension: bool = False) -> dict[str, str]:
    result = {}
    for key, tenant in (("full", None), ("tenant_1", 1), ("tenant_2", 2)):
        sql = fixture_sql(tenant, oracle=tenant is not None)
        if null_extension:
            for t in (1, 2):
                if tenant is None or tenant == t:
                    sql += f"INSERT INTO invoices VALUES ({t * 1000 + 7}, {t}, {t * 100 + 3}, NULL, {t * 100 + 7}, 'A');\n"
        sql += "CREATE VIEW invoice_view AS SELECT id, tenant_id, total FROM invoices;\n"
        result[key] = sql
    return result


def original(tenant: int) -> list[Probe]:
    return [Probe(c.id, c.kind, c.sql, c.expected, family="original") for c in cases(tenant)]


def edge_cases(dialect: str, tenant: int) -> list[Probe]:
    schema = "public" if dialect == "postgres" else "main"
    allowed = [
        ("qualified_table", f"SELECT id, total FROM {schema}.invoices ORDER BY id"),
        (
            "qualified_column",
            f"SELECT {schema}.invoices.id FROM {schema}.invoices ORDER BY {schema}.invoices.id",
        ),
        ("quoted", 'SELECT "id", "total" FROM "invoices" ORDER BY "id"'),
        ("capitalized", "SELECT ID, TOTAL FROM INVOICES ORDER BY ID"),
        ("alias_shadows_hidden", "SELECT name AS email FROM customers ORDER BY email"),
        ("constant_alias", "SELECT 1 AS email FROM customers"),
        ("ordinal_order", "SELECT id, total FROM invoices ORDER BY 1"),
        ("ordinal_group", "SELECT branch_id, COUNT(*) FROM invoices GROUP BY 1 ORDER BY 1"),
        (
            "using_public",
            "SELECT i.id, c.name FROM invoices i JOIN customers c USING (tenant_id) ORDER BY i.id, c.name",
        ),
        ("natural_public", "SELECT i.id FROM invoices i NATURAL JOIN customers c ORDER BY i.id"),
        (
            "recursive",
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<4) SELECT x FROM n ORDER BY x",
        ),
        (
            "cte_shadow",
            "WITH customers AS (SELECT id, total FROM invoices) SELECT id FROM customers ORDER BY id",
        ),
        (
            "left_join_null",
            "SELECT i.id, b.name FROM invoices i LEFT JOIN branches b ON b.id=i.branch_id ORDER BY i.id",
        ),
        ("nested_count", "SELECT (SELECT COUNT(*) FROM invoices) AS n"),
        (
            "cte_aggregate",
            "WITH report AS (SELECT SUM(total) AS s FROM invoices) SELECT s FROM report",
        ),
        ("t_star_public", "SELECT i.* FROM invoices i ORDER BY i.id"),
        (
            "cte_star_safe",
            "WITH c AS (SELECT id, name FROM customers) SELECT c.* FROM c ORDER BY c.id",
        ),
        ("explicit_filtered_view", "SELECT id, total FROM invoice_view ORDER BY id"),
    ]
    if dialect != "sqlite":
        allowed.append(
            (
                "lateral_public",
                "SELECT c.id, q.n FROM customers c CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM invoices i WHERE i.customer_id=c.id) q ORDER BY c.id",
            )
        )
    denied = [
        ("qualified_hidden", f"SELECT c.email FROM {schema}.customers c"),
        ("quoted_hidden", 'SELECT c."email" FROM "customers" c'),
        ("alias_star_hidden", "SELECT c.* FROM customers c"),
        (
            "cte_renamed_hidden",
            "WITH x AS (SELECT email AS name FROM customers) SELECT name FROM x",
        ),
        ("ordinal_hidden", "SELECT email FROM customers ORDER BY 1"),
        ("hidden_group_ordinal", "SELECT email, COUNT(*) FROM customers GROUP BY 1"),
        ("hidden_using", "SELECT c.id FROM customers c JOIN customers d USING (email)"),
        ("hidden_expression", "SELECT id FROM customers ORDER BY LOWER(email)"),
        ("view_not_allowlisted", "SELECT id FROM invoice_view"),
        ("other_schema", "SELECT id FROM other_schema.invoices"),
        (
            "forbidden_cte_shadow",
            "WITH invoices AS (SELECT token FROM secrets) SELECT token FROM invoices",
        ),
        ("root_delete", "DELETE FROM invoices"),
        ("stacked", "SELECT 1; DELETE FROM invoices"),
    ]
    if dialect == "sqlite":
        denied.extend(
            (
                ("catalog", "SELECT name FROM sqlite_master"),
                ("file_function", "SELECT load_extension('__nonexistent_study_extension__')"),
            )
        )
    elif dialect == "duckdb":
        denied.extend(
            (
                ("catalog", "SELECT table_name FROM information_schema.tables"),
                ("file_function", "SELECT * FROM read_csv('__nonexistent_study_fixture__.csv')"),
                (
                    "parquet_function",
                    "SELECT * FROM read_parquet('__nonexistent_study_fixture__.parquet')",
                ),
            )
        )
    else:
        denied.extend(
            (
                ("catalog", "SELECT relname FROM pg_catalog.pg_class"),
                ("catalog_information", "SELECT table_name FROM information_schema.tables"),
                ("file_function", "SELECT pg_read_file('__nonexistent_study_fixture__')"),
                ("row_lock", "SELECT id FROM invoices FOR UPDATE"),
            )
        )
    return [
        Probe(
            "edge_" + name,
            "edge_allowed",
            sql,
            family=name,
            allow_view=name == "explicit_filtered_view",
        )
        for name, sql in allowed
    ] + [
        Probe("edge_" + name, "edge_forbidden", sql, "reject", False, name) for name, sql in denied
    ]


FAMILIES = (
    "projection",
    "predicate",
    "aggregate",
    "group",
    "limit",
    "cte",
    "derived",
    "self_join",
    "left_join",
    "correlated",
    "exists",
    "union",
    "window",
    "qualified",
    "qualified_column",
    "quoted",
    "natural_public",
    "alias_shadow",
    "tautology",
    "hidden",
    "hidden_predicate",
    "forbidden_table",
    "keyword_literal",
)


def generated(n: int, seed: int, dialect: str) -> list[tuple[int, Probe]]:
    rng = random.Random(seed)
    schema = "public" if dialect == "postgres" else "main"
    result = []
    for index in range(n):
        family = rng.choice(FAMILIES)
        tenant = rng.choice((1, 2))
        threshold = rng.randint(80, 220)
        alias = "q" + str(rng.randint(0, 9999))
        depth = rng.randint(1, 3)
        limit = rng.randint(1, 30)
        templates = {
            "projection": f"SELECT id, total FROM invoices ORDER BY id LIMIT {limit}",
            "predicate": f"SELECT id, total FROM invoices WHERE total > {threshold} ORDER BY id",
            "aggregate": f"SELECT COUNT(*) AS {alias}, SUM(total) AS total FROM invoices WHERE total > {threshold}",
            "group": f"SELECT branch_id, COUNT(*) AS {alias} FROM invoices WHERE total > {threshold} GROUP BY branch_id ORDER BY branch_id",
            "limit": f"SELECT id, tenant_id FROM invoices ORDER BY id LIMIT {limit}",
            "cte": f"WITH {alias} AS (SELECT id, total FROM invoices WHERE total > {threshold}) SELECT * FROM {alias} ORDER BY id",
            "derived": f"SELECT {alias}.id FROM (SELECT id FROM invoices WHERE total>{threshold}) {alias} ORDER BY {alias}.id",
            "self_join": f"SELECT a.id, b.tenant_id FROM invoices a JOIN invoices b ON a.id=b.id WHERE a.total>{threshold} ORDER BY a.id",
            "left_join": f"SELECT i.id, b.name FROM invoices i LEFT JOIN branches b ON i.branch_id=b.id WHERE i.total>{threshold} ORDER BY i.id",
            "correlated": f"SELECT c.id, (SELECT COUNT(*) FROM invoices i WHERE i.customer_id=c.id AND i.total>{threshold}) AS {alias} FROM customers c ORDER BY c.id",
            "exists": f"SELECT c.id FROM customers c WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id=c.id AND i.total>{threshold}) ORDER BY c.id",
            "union": f"SELECT id FROM invoices WHERE total>{threshold} UNION ALL SELECT id FROM invoices WHERE tenant_id={3 - tenant} ORDER BY id",
            "window": f"SELECT id, ROW_NUMBER() OVER (ORDER BY total,id) AS {alias} FROM invoices ORDER BY id",
            "qualified": f"SELECT id, total FROM {schema}.invoices WHERE total>{threshold} ORDER BY id",
            "qualified_column": f"SELECT {schema}.invoices.id FROM {schema}.invoices WHERE {schema}.invoices.total>{threshold} ORDER BY {schema}.invoices.id",
            "quoted": f'SELECT "id", "total" FROM "invoices" WHERE "total">{threshold} ORDER BY "id"',
            "natural_public": f"SELECT i.id FROM invoices i NATURAL JOIN customers c WHERE i.total>{threshold} ORDER BY i.id",
            "alias_shadow": "SELECT name AS email FROM customers ORDER BY email",
            "tautology": f"SELECT id, tenant_id FROM invoices WHERE tenant_id={3 - tenant} OR total>{threshold} ORDER BY id",
            "hidden": f"SELECT email AS {alias} FROM customers ORDER BY id",
            "hidden_predicate": f"SELECT id FROM customers WHERE email > '{alias}' ORDER BY id",
            "forbidden_table": f"SELECT token AS {alias} FROM secrets",
            "keyword_literal": f"SELECT 'DELETE DROP {alias}' AS note",
        }
        sql = templates[family]
        if family == "derived":
            for d in range(depth - 1):
                sql = f"SELECT z{d}.id FROM ({sql}) z{d} ORDER BY z{d}.id"
        denied = family in {"hidden", "hidden_predicate", "forbidden_table"}
        result.append(
            (
                tenant,
                Probe(
                    f"fuzz_{index:05d}",
                    "generated",
                    sql,
                    "reject" if denied else "allow",
                    not denied,
                    family,
                ),
            )
        )
    return result


def as_manifest(probes: list[tuple[int, Probe]]) -> list[dict]:
    return [{"tenant": tenant, **asdict(probe)} for tenant, probe in probes]
