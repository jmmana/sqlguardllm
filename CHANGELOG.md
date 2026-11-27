# Changelog

All notable changes to sqlguardllm are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/). Violation codes are stable across minor versions.

## [Unreleased]

### Fixed
- Node.js: a program that ends with a top-level `await guard.close()` on a local-mode guard exited with an unsettled await (code 13), because the engine process is unref'd and nothing kept the event loop alive while `close()` waited for it to exit. `close()` now keeps the loop alive until the engine exits (at most 2 s).
- .NET: `dotnet pack` failed with NU5123 once the engines were staged, and a RID-specific publish (`dotnet publish -r <rid>`, self-contained, single-file, container images) failed with NETSDK1152 or lost the engine folder, because the SDK flattens native assets. The package now ships `build/SqlGuardLlm.targets`, which keeps the `sqlguardllm-engine/` folder intact next to the app (#76).
- The NuGet release job pushed `SqlGuardLlm` without the engines (nothing staged them) and did not push the integration packages (#76, #79).

### Added
- .NET release and CI (#76, #77, #79): `clients/dotnet/build/stage-engines.py` (it was documented but missing) stages the attested engine archives with an `engine.sha256` manifest and fails when the package passes 150 MB; `clients/dotnet/Directory.Build.props` holds one version for the four packages, with SourceLink, deterministic builds and `.snupkg` symbols for all of them, and `release.yml` checks it against the engine release, verifies the engine archives, packs and pushes `SqlGuardLlm`, `SqlGuardLlm.Extensions`, `SqlGuardLlm.EntityFrameworkCore` and `SqlGuardLlm.SemanticKernel`, after a consumer project restored the packed package and started its engine. `dotnet-client.yml` now runs the unit tests and the whole conformance corpus in local mode on Linux, macOS and Windows (a missing engine fails instead of skipping), and a `package` job builds the engine of each platform, packs, and runs `clients/dotnet/smoke` with `dotnet run`, `dotnet publish -r <rid>` (framework-dependent, self-contained single-file, trimmed and Native AOT with trimming and AOT warnings as errors), a tampered-engine check and, on Linux, a container image.
- Node.js recipes (#74): the `Recipes` workflow has a `node-recipes` job that runs `examples/node` with the real engine in local mode on SQLite and on a PostgreSQL 16 service container. `examples/node/run.mjs` and the tests accept `SQLGUARD_ENGINE_CMD`.
- Node.js and .NET guides, quickstart pointers, package chooser and READMEs (English and Spanish) now describe the embedded engine (`SqlGuard.local`, `SqlGuard.CreateLocalAsync`) as the default path and the server as the remote mode.
- **.NET integrations (#78).** `SqlGuardLlm.Extensions` (`services.AddSqlGuard(...)` with options bound from configuration, ADO.NET and Dapper helpers, a Microsoft.Extensions.AI `GuardedSqlFunction`), `SqlGuardLlm.EntityFrameworkCore` (a `DbCommandInterceptor` that enforces raw SQL) and `SqlGuardLlm.SemanticKernel` (a function invocation filter), plus runnable examples in `examples/dotnet/`.
- Node.js recipes (#74) in `examples/node/`: a guarded `sql_db_query` tool for LangChain.js, a guarded `tool()` for the Vercel AI SDK and runners for `pg`, `mysql2` and Prisma `$queryRawUnsafe`, all through `enforce()`, with SQLite and (with `DATABASE_URL`) PostgreSQL tests. See `docs/RECIPES.md`.

## [0.5.0a5] - 2026-11-27

First release whose npm package carries the embedded engine: `npm install sqlguardllm` can run it with no Python and no server (`SqlGuard.local`). The .NET package ships the engine as runtime assets with a local mode.

- npm: the release now publishes the five `sqlguardllm-engine-<platform>` packages and then `sqlguardllm` with the embedded engine in its `optionalDependencies`, all with `npm publish --provenance` (#75). The engines are the attested archives of the same run. `clients/node/npm/publish.mjs --dry-run` runs `npm pack` on all six; the new `npm pack (dry run)` workflow runs it on pull requests and uploads the file lists. The one-time owner steps (Trusted Publisher on each new package) are in CONTRIBUTING.md.

## [0.5.0a4] - 2026-11-27

Security release: upgrade if you use `dialect="duckdb"` with hidden or masked columns. It also brings the embedded-engine groundwork (engine protocol, standalone binaries, npm and NuGet engine packages), the opt-in audit log and the LLM red team.

### Security
- **On DuckDB, hidden and masked columns could be read with `COLUMNS('regex')` or `COLUMNS(lambda)`.** DuckDB picks the columns by pattern at run time, and the guard did not treat it as a star, so `SELECT COLUMNS('.*') FROM customers` returned a hidden column. `COLUMNS()` is now refused whenever the query can see a table with hidden or masked columns (also inside an aggregate). Affects every release with `dialect="duckdb"`. Found by the LLM red team (#63).


### Added
- .NET: local mode (#77). `await using var guard = await SqlGuard.CreateLocalAsync(options)` runs the packaged engine behind the same API and result types as `SqlGuardClient` (both implement the new `ISqlGuard`: `CheckAsync`, `EnforceAsync`, `ExplainAsync`). It is thread-safe (concurrent calls share one engine process, answers matched by request id), takes a `CancellationToken` on every call, has a per-call timeout and is `IAsyncDisposable`. If the engine crashes or a call times out, pending calls fail closed, calls during the backoff fail with `engine_restarting`, and the next call restarts a re-verified engine with exponential backoff. JSON is source-generated and the library is annotated for trimming and Native AOT. It replaces `SqlGuardEngine` from #76 (`SqlGuardEngine.StartAsync(policy)` becomes `SqlGuard.CreateLocalAsync(new SqlGuardLocalOptions { PolicyJson = ... })`). The conformance corpus also runs in local mode (`SQLGUARD_ENGINE_CMD`).
- LLM red team (#63): `research/redteam/run.py` asks Claude (`claude-opus-5-5`, adaptive thinking, structured outputs, server-side fallback) for attacks against the guard, with a fixed defensive-purpose prompt (`prompt.md`, sha256 recorded). Every query is kept in `research/redteam/<date>/attacks.jsonl` with model, prompt and policy hashes, version, git sha, verdict and per-engine outcome, judged by the fuzzing canary oracle on SQLite, DuckDB, PostgreSQL, MySQL and SQL Server; duplicates are fingerprinted and counted, bypasses become pending cases for `tests/corpus/redteam.yaml`. A run is capped (at most 50 attacks, a hard token budget, a per-run USD ceiling checked against a free token count first); a refusal is recorded, not a failure. `--rescore <dir>` re-judges a stored dataset without the API. Weekly and manual workflow `.github/workflows/redteam.yml` on `main` only.
- .NET: the engine as runtime assets of the NuGet package (#76). `SqlGuardEngine.StartAsync(policy)` runs the packaged `runtimes/{rid}/native/sqlguardllm-engine/` for linux-x64, linux-arm64, osx-x64, osx-arm64 and win-x64 over the stdio protocol, after recomputing the SHA-256 of every file against `engine.sha256` (a mismatch, a missing file or a wrong engine version or protocol refuses to run). An unsupported runtime (other OS or CPU, musl) fails with `engine_unsupported`, which names remote mode (`SqlGuardClient`). `clients/dotnet/build/stage-engines.py` stages the output of `fetch-engine.py` and fails when the `.nupkg` passes 150 MB.
- Standalone engine binaries for linux-x64, linux-arm64, macos-arm64, macos-x64 and windows-x64 (#69): PyInstaller one-dir builds of `engine --stdio` (sqlguardllm, sqlglot and the standard library only) built by `scripts/build-engine.py` on a native runner each and accepted only after `hello` and the full conformance corpus pass over stdio (`.github/workflows/engine-binaries.yml`, also on every pull request touching the engine). Size, cold start and memory in `benchmarks/engine-binaries.json`.
- Release pipeline for the binaries (#70): on every `v*` tag the five archives and `SHA256SUMS` are attached to the GitHub Release from the same commit as the PyPI release, with build provenance attestations (`gh attestation verify`); the registries publish only after all binaries passed. `scripts/fetch-engine.py <version>` downloads and verifies them (checksums, attestations, lockstep version) and fails on any mismatch. Asset names, verification and the client version rule (refuse an engine with another major or protocol version) are in `docs/PROTOCOL.md`.
- `sqlguardllm engine --stdio`: the engine as a child process over newline-delimited JSON (protocol version 1: `hello`, `load_policy`, `check`, `explain`, `shutdown`), with the same `check` and `explain` bodies as the HTTP server, described by `schema/protocol/protocol.schema.json` and `docs/PROTOCOL.md`. The conformance corpus runs over it (#68).
- Opt-in audit log of every decision, off by default (#65). `Guard(policy, audit=AuditLog(...))` or any callable taking an `AuditEvent`; sinks for a JSON Lines file, stderr, `logging` and OpenTelemetry (`sqlguardllm[otel]`). Events carry the timestamp, id, version, policy fingerprint, role, outcome, violation codes, tables, normalised-SQL fingerprint, duration and the names of the context keys, never their values. `include_sql` is `redacted` (default, literals replaced by `?`), `none` or `raw`. A failing sink never allows a rejected query; with `strict=True` it rejects the query with the new code `audit_failure`. CLI `check`, `serve` and `mcp` get `--audit-log PATH|-` and `--audit-sql`. See `docs/AUDIT.md`.

## [0.5.0a3] - 2026-11-26

Security release: upgrade if you use `dialect="mysql"` with hidden or masked columns.

### Security
- **On MySQL, hidden and masked columns could be read by writing the column name in another case.** MySQL ignores the case of column names, quoted or not, but sqlglot's MySQL dialect is case-sensitive (because MySQL table names can be), so with `customers.email` hidden `SELECT EMAIL FROM customers` was allowed, and a masked `PHONE` was neither masked nor refused outside the select list. Hidden and masked columns are now matched case-insensitively in every dialect, which can only reject or mask more. Affects 0.1.0a1 to 0.5.0a2 with `dialect="mysql"`: upgrade. Found by the new fuzzer (#62).

### Added
- `sqlguardllm test` checks declared policy cases and reports results as text or JUnit XML (#66).
- `examples/roles-tests.yaml` tests every role of the demo policy set, next to `examples/policy-tests.yaml` for the demo policy. CI runs both and lints the demo policies with `--strict` (#66, #67).
- `sqlguardllm lint` warns about policies that are valid but probably unsafe, with stable codes: `no-table-allowlist`, `tenant-without-row-filter`, `sensitive-column-exposed`, `max-rows-disabled`, `max-rows-high` (above 10000 or `--max-rows-threshold`), `aggregate-k-low` (k below 5) and `denied-function-allowed`. Each warning has an explanation, a fix and a link to its field in the policy reference, in English or Spanish (`--language`), as text or JSON (`--format`). Policy sets are linted role by role. The column checks read the policy's `schema` and are reported as skipped without it. `--strict` exits 1 on warnings (#67).
- Policy key `lint_suppress: {code: reason}` silences a reviewed lint warning, and the report shows the reason. It is in the policy and policy-set JSON Schemas, unknown codes and blank reasons are rejected, roles inherit and extend it, and checking SQL ignores it (#67).
- Property-based fuzzing of the guard, judged by executing every allowed query on canary data (`tests/fuzz/`, `hypothesis`): no value of another role's rows or of a hidden or masked column in the output, the same rows when only secrets change, never more than `max_rows`, no exception from `check()`, and idempotence. 300 examples on SQLite and DuckDB in every pull request; 20,000 every night on PostgreSQL 16, MySQL 8, SQL Server 2022, DuckDB and SQLite. Failures are minimised into `tests/corpus/fuzz_regressions.yaml`; statistics in `research/results/fuzz.json`; the oracle and its limits in `docs/THREAT_MODEL.md`. It also found that DuckDB 2.0.1 drops the `LIMIT` of some `LEFT JOIN` queries, so there `max_rows` depends on the engine (#62).

### Fixed
- `check()` raised instead of rejecting on some malformed input: `RecursionError` for deeply nested SQL (thousands of parentheses), and `TypeError` from sqlglot's argument checks, which are on whenever `unittest` or `pytest` is imported (a T-SQL query whose nested comments leave `LEN` with two arguments). Any such error while parsing is now `parse_error`. Found by the fuzzer (#62).

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

[Unreleased]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a5...HEAD
[0.5.0a5]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a4...v0.5.0a5
[0.5.0a4]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a3...v0.5.0a4
[0.5.0a3]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a2...v0.5.0a3
[0.5.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.5.0a1...v0.5.0a2
[0.5.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.4.0a1...v0.5.0a1
[0.4.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.3.0a1...v0.4.0a1
[0.3.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a2...v0.3.0a1
[0.2.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a2...v0.2.0a1
[0.1.0a2]: https://github.com/jmmana/sqlguardllm/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/jmmana/sqlguardllm/releases/tag/v0.1.0a1
