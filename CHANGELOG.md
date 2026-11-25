# Changelog

All notable changes to sqlguard are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/). Violation codes are stable across minor versions.

## [Unreleased]

## [0.1.0a1] - 2026-11-25

First alpha. Install with `pip install sqlguardllm`; import with `import sqlguard`.

### Added

- Public API: `Guard`, `Policy`, `CheckResult`, `Violation`, `GuardViolation`, `PolicyError` and `ViolationCode` with 11 stable codes. `Guard.check()`, `Guard.enforce()` and `Guard.wrap()`.
- Rule 1–3: fail-closed parsing, a single query root, and a read-only walk of the whole tree (writes, DDL, `SELECT … INTO`, `COPY`, `ATTACH`, `PRAGMA`, `EXEC`, `SET`, `CALL`, row locks), including writes hidden in CTEs.
- Rule 4: table allowlist with CTE scoping, per-dialect identifier normalization, `default_schema`, rejection of table functions, and a fail-closed check that every table reference was classified.
- Rule 5: hidden columns in every clause, through `*` and `t.*`, whole-row references, `USING`, `NATURAL JOIN` and correlated subqueries.
- Rule 6: built-in denied functions for PostgreSQL, MySQL, SQL Server, Oracle, SQLite and DuckDB (file readers, sleeps, shells, network calls, functions that run SQL from a string such as `query_to_xml`), qualified names like `SYS.DBMS_PIPE.*`, and an allowlist mode.
- Rule 7: row filters injected as derived tables in every occurrence of a table, with context values bound as escaped literals; missing or unsupported values fail closed.
- Rule 8: row limit in each dialect's syntax (`LIMIT`, `TOP`, `FETCH FIRST`); set operations are limited as a whole.
- Messages in English and Spanish.
- `Policy.from_dict()` and `Policy.from_file()` for JSON, and YAML with the `sqlguardllm[yaml]` extra.
- An adversarial corpus of 64 cases in `tests/corpus/`, and row-filter tests that execute the rewritten SQL on SQLite.

### Known limitations

- Without table metadata, an unqualified column that shares its name with a hidden column is rejected (conservative). Exact checking with `Policy(schema=...)` is planned.
- Table functions are rejected, even harmless ones like `generate_series`.
- No command-line tool yet (planned for v0.2).

[Unreleased]: https://github.com/jmmana/sqlguard/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/jmmana/sqlguard/releases/tag/v0.1.0a1
