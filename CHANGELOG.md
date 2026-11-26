# Changelog

All notable changes to sqlguardllm are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/). Violation codes are stable across minor versions.

## [Unreleased]

### Added

- `masked_columns`: a column can be selected but comes back masked (`null`, `redact`, `hash` as SHA-256 hex per dialect, `partial`), even inside functions or `CASE`; using it anywhere outside the select list is `hidden_column`. With the new `schema` option, `*` over such a table is expanded with the masks (#29).
- `aggregate_only: {table: k}`: the table can only be read through aggregates and `GROUP BY` (new code `aggregate_required`), and the guard adds `HAVING COUNT(*) >= k` so no group smaller than k rows comes back (#30).

## [0.2.0a2] - 2026-11-25

### Security

- **Hidden columns in `HAVING`, `QUALIFY` and inside `ORDER BY`/`DISTINCT ON` expressions were not checked when unqualified.** sqlglot leaves those columns out of a scope's column list because they could be select aliases, so `SELECT COUNT(*) FROM customers HAVING MAX(email) > 'm'` was allowed and, repeated, leaked the column one comparison at a time. They are now checked conservatively as source columns; a bare alias in `ORDER BY` keeps working. Affects 0.1.0a1 to 0.2.0a1: upgrade.

### Added

- Command-line tool: `sqlguardllm check --policy FILE [--context JSON|@file] [--dialect D] [--language es] [--format json] [SQL | -]`, with exit codes 0 (allowed), 1 (rejected) and 2 (usage or policy error). Also `python -m sqlguardllm` (#12).
- JSON Schema of policy files in `schema/policy.schema.json`, printed by `sqlguardllm schema` and kept in sync by a test; `examples/policy.yaml` points editors to it (#11).

## [0.2.0a1] - 2026-11-25

Optional limits for queries that are far more expensive than the question that produced them. All new options are off by default: existing policies behave exactly the same.

### Added

- Complexity limits, all off by default: `max_joins`, `max_subquery_depth` and `forbid_cartesian`, with the new violation code `too_complex` (#23).
- `required_filters`: a table can only be read with a predicate on one of the given columns, such as a date range on a fact table, with the new code `missing_required_filter` (#24).
- `output_dialect`: parse in the policy's dialect and generate the rewritten SQL in another one; limits render as `TOP` or `FETCH FIRST` where needed (#25).
- Corpus cases can set their own policy options with a `policy` key.

## [0.1.0a2] - 2026-11-25

One name everywhere: the project is now **sqlguardllm**.

### Changed

- The import package is now `sqlguardllm` (`from sqlguardllm import Guard, Policy`), matching the PyPI name. `import sqlguard` from 0.1.0a1 no longer works.
- The repository moved to [jmmana/sqlguardllm](https://github.com/jmmana/sqlguardllm); the old URL redirects.

## [0.1.0a1] - 2026-11-25

First alpha. Installed with `pip install sqlguardllm` and imported as `import sqlguard`.

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

[Unreleased]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a2...HEAD
[0.2.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a2...v0.2.0a1
[0.1.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/jmmana/sqlguardllm/releases/tag/v0.1.0a1
