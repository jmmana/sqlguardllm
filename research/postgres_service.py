"""Optional native PostgreSQL client/server backend for isolated CI fixtures."""

from __future__ import annotations

import os
import uuid


class PostgresBackend:
    name = "postgres_service"

    def __init__(self, fixtures: dict[str, str]) -> None:
        import psycopg  # noqa: PLC0415 -- optional research dependency
        from psycopg import conninfo, sql  # noqa: PLC0415

        self.sql = sql
        dsn = os.environ["SQLGUARD_RESEARCH_PG_DSN"]
        self.admin = psycopg.connect(dsn, autocommit=True)
        self.connections, self.databases = {}, []
        prefix = "sg_research_" + uuid.uuid4().hex[:12]
        for key, fixture in fixtures.items():
            name = prefix + "_" + key
            self.admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            self.databases.append(name)
            conn = psycopg.connect(conninfo.make_conninfo(dsn, dbname=name), autocommit=True)
            conn.execute(fixture)
            self.connections[key] = conn
        full = self.connections["full"]
        self.engine_version = full.execute("SELECT version()").fetchone()[0]
        for tenant in (1, 2):
            role = f"study_tenant_{tenant}"
            if not self.admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone():
                self.admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            full.execute(
                sql.SQL("GRANT SELECT ON invoices, branches TO {}").format(sql.Identifier(role))
            )
            full.execute(
                sql.SQL("GRANT SELECT (id, tenant_id, name) ON customers TO {}").format(
                    sql.Identifier(role)
                )
            )
        for table in ("invoices", "customers", "branches"):
            full.execute(
                sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(sql.Identifier(table))
            )
            for tenant in (1, 2):
                full.execute(
                    sql.SQL(
                        "CREATE POLICY {} ON {} FOR SELECT TO {} USING (tenant_id = {})"
                    ).format(
                        sql.Identifier(f"tenant_{tenant}"),
                        sql.Identifier(table),
                        sql.Identifier(f"study_tenant_{tenant}"),
                        sql.Literal(tenant),
                    )
                )

    def query(self, key: str, query: str, *, role=None, fetch_limit: int = 9) -> list:
        conn = self.connections[key]
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            if role:
                if role not in {"study_tenant_1", "study_tenant_2"}:
                    raise ValueError("Unexpected study role")
                conn.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(role)))
            return [list(row) for row in conn.execute(query).fetchmany(fetch_limit)]

    def close(self) -> None:
        for conn in self.connections.values():
            conn.close()
        for name in self.databases:
            self.admin.execute(self.sql.SQL("DROP DATABASE {}").format(self.sql.Identifier(name)))
        self.admin.close()
