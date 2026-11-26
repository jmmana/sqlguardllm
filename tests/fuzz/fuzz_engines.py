"""The engines the fuzzer executes on, each holding world A and world B (see fuzz_schema).

SQLite and DuckDB run in-process. PostgreSQL, MySQL and SQL Server run when their URL is set
(``SQLGUARD_PG_URL``, ``SQLGUARD_MYSQL_URL``, ``SQLGUARD_MSSQL_URL``, as in tests/engines), in
two schemas or databases named ``sgfuzz_a`` and ``sgfuzz_b``. Every session is read-only where
the engine allows it and every query has a timeout, so a guard bug can't damage the worlds.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import threading
import time
from typing import Any

from fuzz_schema import MAX_ROWS, TABLES, Row, ddl, inserts, normalize_rows, rows

TIMEOUT_S = 5.0
FETCH_CAP = MAX_ROWS * 20 + 1  # enough to see any max_rows breach without fetching a cross join
WORLDS = ("A", "B")
URLS = {
    "postgres": "SQLGUARD_PG_URL",
    "mysql": "SQLGUARD_MYSQL_URL",
    "tsql": "SQLGUARD_MSSQL_URL",
}
ALL_ENGINES = ("sqlite", "duckdb", "postgres", "mysql", "tsql")


class EngineError(Exception):
    """The engine refused or failed to run the query (not a guard failure by itself)."""


def _script(world: str) -> list[str]:
    data = rows(world)
    statements: list[str] = []
    for table in TABLES:
        statements.append(ddl(table))
        statements.extend(inserts(table, data[table]))
    return statements


class Backend:
    name = ""
    dialect = ""

    def query(self, world: str, sql: str) -> list[Row]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SQLiteBackend(Backend):
    name = dialect = "sqlite"

    def __init__(self) -> None:
        self.conns: dict[str, sqlite3.Connection] = {}
        for world in WORLDS:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            for statement in _script(world):
                conn.execute(statement)
            conn.commit()
            conn.execute("PRAGMA query_only = ON")
            self.conns[world] = conn

    def query(self, world: str, sql: str) -> list[Row]:
        conn = self.conns[world]
        deadline = time.monotonic() + TIMEOUT_S
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            return normalize_rows(conn.execute(sql).fetchmany(FETCH_CAP))
        except (sqlite3.Error, OverflowError, ValueError) as exc:
            raise EngineError(str(exc)) from exc
        finally:
            conn.set_progress_handler(None, 0)
            if conn.in_transaction:
                conn.rollback()

    def close(self) -> None:
        for conn in self.conns.values():
            conn.close()


class DuckDBBackend(Backend):
    name = dialect = "duckdb"

    def __init__(self) -> None:
        import duckdb  # noqa: PLC0415

        self.duckdb = duckdb
        self.temp = tempfile.TemporaryDirectory(prefix="sqlguard-fuzz-")
        self.conns: dict[str, Any] = {}
        for world in WORLDS:
            path = os.path.join(self.temp.name, f"{world}.duckdb")
            conn = duckdb.connect(path)
            for statement in _script(world):
                conn.execute(statement)
            conn.close()
            conn = duckdb.connect(path, read_only=True, config={"threads": 1})
            # The perfect hash aggregate is chosen from column min/max statistics, which differ
            # between the worlds, and changes the order of groups: keep plans independent of data.
            conn.execute("SET perfect_ht_threshold = 0")
            self.conns[world] = conn

    def query(self, world: str, sql: str) -> list[Row]:
        conn = self.conns[world]
        timer = threading.Timer(TIMEOUT_S, conn.interrupt)
        timer.start()
        try:
            return normalize_rows(conn.execute(sql).fetchmany(FETCH_CAP))
        except (self.duckdb.Error, OverflowError, ValueError) as exc:
            raise EngineError(str(exc)) from exc
        finally:
            timer.cancel()

    def close(self) -> None:
        for conn in self.conns.values():
            conn.close()
        self.temp.cleanup()


class RemoteBackend(Backend):
    """PostgreSQL, MySQL or SQL Server through SQLAlchemy's raw DB-API connections."""

    def __init__(self, dialect: str, url: str) -> None:
        import sqlalchemy as sa  # noqa: PLC0415

        self.name = self.dialect = dialect
        connect_args: dict[str, Any] = {}
        if dialect == "tsql":
            connect_args = {"timeout": int(TIMEOUT_S) * 2, "login_timeout": 30}
        self.engine = sa.create_engine(url, connect_args=connect_args, poolclass=sa.pool.NullPool)
        self.error: type[BaseException] = self.engine.dialect.loaded_dbapi.Error
        self.conns: dict[str, Any] = {}
        for world in WORLDS:
            self.conns[world] = self._open(f"sgfuzz_{world.lower()}", world)

    def _run(self, conn: Any, *statements: str) -> None:
        cursor = conn.cursor()
        for statement in statements:
            cursor.execute(statement)
        cursor.close()

    def _open(self, name: str, world: str) -> Any:
        conn = self.engine.raw_connection()
        drops = [f"DROP TABLE IF EXISTS {table}" for table in reversed(TABLES)]
        if self.dialect == "postgres":
            self._run(
                conn,
                f"DROP SCHEMA IF EXISTS {name} CASCADE",
                f"CREATE SCHEMA {name}",
                f"SET search_path TO {name}",
            )
            self._run(conn, *_script(world))
            conn.commit()
            self._run(
                conn,
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                f"SET statement_timeout = {int(TIMEOUT_S * 1000)}",
            )
            conn.commit()
        elif self.dialect == "mysql":
            self._run(conn, f"CREATE DATABASE IF NOT EXISTS {name}", f"USE {name}", *drops)
            self._run(conn, *_script(world))
            conn.commit()
            self._run(
                conn,
                "SET SESSION TRANSACTION READ ONLY",
                f"SET SESSION max_execution_time = {int(TIMEOUT_S * 1000)}",
            )
            conn.commit()
        else:  # SQL Server: CREATE DATABASE can't run in a transaction
            driver = conn.driver_connection
            driver.autocommit(True)
            self._run(
                conn,
                f"IF DB_ID('{name}') IS NULL CREATE DATABASE {name}",
                f"USE {name}",
                *drops,
                *_script(world),
            )
            driver.autocommit(False)
        return conn

    def query(self, world: str, sql: str) -> list[Row]:
        conn = self.conns[world]
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            fetched = cursor.fetchmany(FETCH_CAP) if cursor.description else []
            return normalize_rows(fetched)
        except (self.error, OverflowError, ValueError) as exc:
            raise EngineError(str(exc).splitlines()[0] if str(exc) else repr(exc)) from exc
        finally:
            with contextlib.suppress(self.error):
                cursor.close()
            conn.rollback()

    def close(self) -> None:
        for conn in self.conns.values():
            conn.close()
        self.engine.dispose()


def open_backend(name: str) -> Backend | None:
    """The backend for ``name``, or None when its URL isn't set."""
    if name == "sqlite":
        return SQLiteBackend()
    if name == "duckdb":
        return DuckDBBackend()
    url = os.environ.get(URLS[name])
    if not url:
        return None
    return RemoteBackend(name, url)
