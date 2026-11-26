# Changelog

All notable changes to sqlguardllm are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/). Violation codes are stable across minor versions.

## [Unreleased]

### Added
- `sqlguardllm test` checks declared policy cases and reports results as text or JUnit XML (#66).
- `examples/roles-tests.yaml` tests every role of the demo policy set, next to `examples/policy-tests.yaml` for the demo policy. CI runs both and lints the demo policies with `--strict` (#66, #67).
- `sqlguardllm lint` warns about policies that are valid but probably unsafe, with stable codes: `no-table-allowlist`, `tenant-without-row-filter`, `sensitive-column-exposed`, `max-rows-disabled`, `max-rows-high` (above 10000 or `--max-rows-threshold`), `aggregate-k-low` (k below 5) and `denied-function-allowed`. Each warning has an explanation, a fix and a link to its field in the policy reference, in English or Spanish (`--language`), as text or JSON (`--format`). Policy sets are linted role by role. The column checks read the policy's `schema` and are reported as skipped without it. `--strict` exits 1 on warnings (#67).
- Policy key `lint_suppress: {code: reason}` silences a reviewed lint warning, and the report shows the reason. It is in the policy and policy-set JSON Schemas, unknown codes and blank reasons are rejected, roles inherit and extend it, and checking SQL ignores it (#67).

## [0.5.0a2] - 2026-11-26

First release on all three registries at once: PyPI, npm and, for the first time, NuGet.

### Added
- Every release now publishes to PyPI, npm and NuGet together with the same version, all with Trusted Publishing.
- Playground: a "Policy rules" panel ("Reglas de la política") of plain-language switches that edit the policy JSON and follow it when the JSON is edited by hand: `customers.email` visible, hidden (the query is rejected) or masked (the query runs and the value comes back as `ce***`; `partial`, `redact` or `null`, since `hash` has no SQLite version), the row filter per branch, `max_rows`, and the `users` table with its `password_hash`. Share links carry the edited policy.
- Playground: every rejection now explains each violation, for every code the engine emits: the rule that fired, why it protects data, how to allow it (with one-click fixes for the demo's own rules) and a link to the field in the policy reference. A "What do these rules mean?" section explains each policy field in plain language. The Playwright test covers both and now serves the folder itself.
- Step-by-step guides for Python, Node.js / TypeScript and .NET, in English and Spanish, linked from "Choose a package": install, start `sqlguardllm serve` for the clients, a program that checks one allowed and one rejected query, its exact output, and how to change a rule. The quickstarts point to them.
- README and README.es: an Install section for every language, and registry badges for PyPI, npm, NuGet and the docs at the top.
- Policy reference: each field has its own anchor, such as `policy-reference/#max_rows`.

### Fixed
- README: "Other languages" still described the server and the npm package as planned, and the HTTP server was said to ship a Docker image; none is published, the `Dockerfile` builds one.
- T-SQL ordered set operations preserve their ordering and use a valid row limit on SQL Server (#92).
- Node.js client: the package declared the MIT license by mistake; it is Apache-2.0 like the rest of the project, and the license file now ships in the package.
- Node.js client: `sqlguardllm/package.json` is exported, so tools can read the installed version.

## [0.5.0a1] - 2026-11-26

Everything that was planned for other languages and tooling: an HTTP server, official Node.js and .NET clients checked against the same 148-case corpus, a documentation site and a browser playground, plus `Policy.from_database`, a cost guard for the MCP server and the rest of the warehouse denylists.

### Added
- In-browser playground "Try to break it" in `playground/` (static site, Pyodide, demo SQLite, gallery presets, two roles, editable policy, shareable URL-fragment links, English/Spanish) with a Playwright test and CI workflow. Not deployed yet (#18).
- Documentation site (mkdocs-material, https://jmmana.github.io/sqlguardllm/): quickstart in English and Spanish, rules, a policy reference generated from `schema/policy.schema.json`, an API reference from docstrings, threat model, attack gallery and integrations; built with `--strict` and link-checked on every pull request, deployed to GitHub Pages from main (#14).
- Optional cost guard for `sqlguardllm mcp` (`--max-cost`, `--max-plan-rows`; `max_cost` / `max_plan_rows` in `create_server`): runs `EXPLAIN` (JSON, never `ANALYZE`) on PostgreSQL and MySQL before a query and refuses plans over the threshold, failing closed if the estimate can't be read; ignored with a warning on other engines. `explain_query` reports the estimate (#33).
- Cross-language conformance: every attack-corpus case runs through the HTTP server, the Node.js client and the .NET client and must equal the in-process `Guard.check` answer (`conformance/cases.json`, a `Conformance` workflow, `docs/CONFORMANCE.md`). The .NET client now sends a `Content-Length` body, which the server requires (#37).
- `sqlguardllm serve`: an HTTP server (`GET /v1/health`, `POST /v1/check`, `POST /v1/explain`) so any language can use the engine as a sidecar, standard library only, optional bearer token, plus a `Dockerfile`; install with `sqlguardllm[serve]`. See `docs/SERVER.md` (#34).
- Official .NET client `SqlGuardLlm` (`clients/dotnet/`, NuGet): `CheckAsync`, `EnforceAsync`, `ExplainAsync` and `HealthAsync` against `sqlguardllm serve`, multi-targeting net8.0 and net10.0, with no dependencies and fail-closed errors. Not published yet (#36).
- Official Node.js/TypeScript client in `clients/node` (`sqlguardllm` on npm, not yet published): `SqlGuard` with `check`, `enforce`, `explain`, `health` and `wrap` for the `sqlguardllm serve` HTTP API; zero runtime dependencies, ESM + CJS, fails closed on network errors, timeouts and malformed responses (#35).
- `Policy.from_database(url, include=…, exclude=…, schemas=…, **options)`: fills `tables` and `schema` by reading a live database with the SQLAlchemy inspector (new optional extra `sqlguardllm[db]`). Only names are read, never rows; errors never include the URL (#28).
- Denied functions for Snowflake, BigQuery, ClickHouse and Trino, each list linked to the vendor documentation, with a corpus case for every entry (#16). Contributed by @shiwng in #42.
- Integration recipes in `docs/RECIPES.md` with runnable code in `examples/`: a generic DB-API wrapper, SQLAlchemy, LangChain (`create_agent` tool and langchain-community's toolkit), LlamaIndex, Vanna 2.0 and a pointer to the MCP server. Each is tested with fake model output on SQLite; a Recipes workflow runs them against the framework versions they target. No framework is a dependency (#17).
- Performance budget: `benchmarks/bench_check.py` times `Guard.check()` on 21 representative queries (median and p95), and a Benchmark workflow fails when a typical query's median is over budget (2 ms on a laptop, 4 ms on GitHub's slower shared runner, where five joins take 3.2–3.4 ms) or a pull request makes a query more than 25 % slower than its base branch (#19).

## [0.4.0a1] - 2026-11-26

An MCP server: any MCP client (Claude Desktop, Cursor, VS Code, agents) gets a database tool that can't do harm. Install it with `pip install "sqlguardllm[mcp]"`.

### Added

- MCP server: `sqlguardllm mcp --policy FILE [--role NAME] [--db URL] [--context JSON|@file] [--language es] [--timeout SECONDS]` (optional extra `sqlguardllm[mcp]`) gives any MCP client the tools `list_tables`, `describe_table`, `explain_query` and `query`. Queries go through `Guard.enforce()` and run in a read-only transaction with a timeout, always rolled back, returning at most `max_rows` rows; violations come back as tool errors the model can act on. The context and the database URL never appear in tool output or logs. Setup for Claude Desktop, Cursor and VS Code, and a demo database, in `docs/MCP.md` and `examples/mcp/` (#31, #32).

## [0.3.0a1] - 2026-11-26

Personal data protection, richer policies and a second security fix. New options are off by default: existing policies behave the same, except that percentage and `WITH TIES` limits are now capped.

### Security

- **`max_rows` could be bypassed with a percentage or `WITH TIES` limit.** `SELECT TOP 50 PERCENT …` (SQL Server), `FETCH FIRST 50 PERCENT ROWS ONLY` (Oracle) and `FETCH FIRST n ROWS WITH TIES` passed unchanged, because sqlglot 30 keeps those flags in `limit_options`. Such limits are now replaced with a plain limit of `max_rows`. Affects 0.1.0a1 to 0.2.0a2: upgrade. Found while building `Guard.explain()`.

### Added

- `masked_columns`: a column can be selected but comes back masked (`null`, `redact`, `hash` as SHA-256 hex per dialect, `partial`), even inside functions or `CASE`; using it anywhere outside the select list is `hidden_column`. With the new `schema` option, `*` over such a table is expanded with the masks (#29).
- `aggregate_only: {table: k}`: the table can only be read through aggregates and `GROUP BY` (new code `aggregate_required`), and the guard adds `HAVING COUNT(*) >= k` so no group smaller than k rows comes back (#30).
- `PolicySet`: a `base` policy and `roles` in one file (`PolicySet.from_file`), with `extends` between roles, `for_role()` and a cached `guard()` per role and language. A key set in a role replaces the inherited value; keys under `add` extend lists and mappings. Cycles and unknown roles are `PolicyError`. Example in `examples/roles.yaml`; JSON Schema in `schema/policy-set.schema.json`, printed by `sqlguardllm schema --policy-set` (#26).
- `Guard.explain(sql, context)`: an `Explanation` with the tables and columns a query reads, the masks, group sizes, row filters and limit it gets, its violations, and a sentence in English or Spanish for UIs and audit logs. Shares one pass with `check()` (#27).

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

[Unreleased]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a2...HEAD
[0.5.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a1...v0.5.0a2
[0.5.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.4.0a1...v0.5.0a1
[0.4.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.3.0a1...v0.4.0a1
[0.3.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a2...v0.3.0a1
[0.2.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a2...v0.2.0a1
[0.1.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/jmmana/sqlguardllm/releases/tag/v0.1.0a1
