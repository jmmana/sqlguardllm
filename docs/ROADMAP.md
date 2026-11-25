# Roadmap

Tracked in [issues](https://github.com/jmmana/sqlguardllm/issues) and [milestones](https://github.com/jmmana/sqlguardllm/milestones). Each issue has a goal, verifiable acceptance criteria, files, size and dependencies, so anyone can pick it up (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## [v0.1 · Core guard](https://github.com/jmmana/sqlguardllm/milestone/1)

Statement and read-only checks, table allowlist, hidden columns, denied functions, row limit, adversarial corpus and messages.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#1](https://github.com/jmmana/sqlguardllm/issues/1) | Package scaffold: src layout, uv, ruff, mypy --strict, pytest and CI matrix | S | — |
| [#2](https://github.com/jmmana/sqlguardllm/issues/2) | Public API types: Policy, CheckResult, Violation and stable violation codes | S | [#1](https://github.com/jmmana/sqlguardllm/issues/1) |
| [#3](https://github.com/jmmana/sqlguardllm/issues/3) | Rules 1-3: fail-closed parsing, single query and read-only tree walk | M | [#2](https://github.com/jmmana/sqlguardllm/issues/2) |
| [#4](https://github.com/jmmana/sqlguardllm/issues/4) | Rule 4: table allowlist with CTE scoping, identifier normalization and table functions | M | [#3](https://github.com/jmmana/sqlguardllm/issues/3) |
| [#6](https://github.com/jmmana/sqlguardllm/issues/6) | Rule 6: denied functions per dialect, qualified names and allowlist mode | M | [#3](https://github.com/jmmana/sqlguardllm/issues/3) |
| [#7](https://github.com/jmmana/sqlguardllm/issues/7) | Rule 8: row limit per dialect (LIMIT, TOP, FETCH FIRST) | S | [#3](https://github.com/jmmana/sqlguardllm/issues/3) |
| [#8](https://github.com/jmmana/sqlguardllm/issues/8) | Adversarial corpus in YAML with a test runner, seeded with the attack gallery | M | [#3](https://github.com/jmmana/sqlguardllm/issues/3) |
| [#9](https://github.com/jmmana/sqlguardllm/issues/9) | Human-readable violation messages in English and Spanish 👋 | S | [#2](https://github.com/jmmana/sqlguardllm/issues/2) |

## [v0.2 · Row-level security & CLI](https://github.com/jmmana/sqlguardllm/milestone/2)

Row filters as derived tables with safe context binding, policy files, CLI, dialect matrix on real engines, docs site and the first PyPI release.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#10](https://github.com/jmmana/sqlguardllm/issues/10) | Rule 7: row filters as derived tables with safe context binding | L | [#4](https://github.com/jmmana/sqlguardllm/issues/4) |
| [#11](https://github.com/jmmana/sqlguardllm/issues/11) | Policy files in YAML and JSON with a published JSON Schema | S | [#2](https://github.com/jmmana/sqlguardllm/issues/2) |
| [#12](https://github.com/jmmana/sqlguardllm/issues/12) | Command-line tool: sqlguardllm check | S | [#11](https://github.com/jmmana/sqlguardllm/issues/11) |
| [#13](https://github.com/jmmana/sqlguardllm/issues/13) | Dialect test matrix against real database engines | L | [#10](https://github.com/jmmana/sqlguardllm/issues/10) [#7](https://github.com/jmmana/sqlguardllm/issues/7) [#6](https://github.com/jmmana/sqlguardllm/issues/6) |
| [#14](https://github.com/jmmana/sqlguardllm/issues/14) | Documentation site with API reference | M | [#12](https://github.com/jmmana/sqlguardllm/issues/12) |
| [#15](https://github.com/jmmana/sqlguardllm/issues/15) | Release automation: PyPI Trusted Publishing and changelog | S | [#1](https://github.com/jmmana/sqlguardllm/issues/1) [#12](https://github.com/jmmana/sqlguardllm/issues/12) |
| [#16](https://github.com/jmmana/sqlguardllm/issues/16) | Extend denied functions for Snowflake, BigQuery, ClickHouse and Trino 👋 | S | [#6](https://github.com/jmmana/sqlguardllm/issues/6) |

## [v0.3 · More power](https://github.com/jmmana/sqlguardllm/milestone/4)

Complexity limits, required filters, PII masking, aggregate-only tables (k-anonymity), exact schema from the database, policies per role, explain() and dialect translation.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#5](https://github.com/jmmana/sqlguardllm/issues/5) | Rule 5: hidden columns in every clause and through stars | M | [#4](https://github.com/jmmana/sqlguardllm/issues/4) |
| [#23](https://github.com/jmmana/sqlguardllm/issues/23) | Complexity limits: max joins, max nesting depth and cartesian products | M | [#3](https://github.com/jmmana/sqlguardllm/issues/3) |
| [#24](https://github.com/jmmana/sqlguardllm/issues/24) | Required filters: a table must be filtered on one of its columns | S | [#4](https://github.com/jmmana/sqlguardllm/issues/4) |
| [#25](https://github.com/jmmana/sqlguardllm/issues/25) | Dialect translation: read in one dialect, generate in another | S | [#2](https://github.com/jmmana/sqlguardllm/issues/2) |
| [#26](https://github.com/jmmana/sqlguardllm/issues/26) | PolicySet: one file with a base policy and roles | S | [#11](https://github.com/jmmana/sqlguardllm/issues/11) |
| [#27](https://github.com/jmmana/sqlguardllm/issues/27) | Guard.explain(): what a query reads, in data and in words | M | [#10](https://github.com/jmmana/sqlguardllm/issues/10) |
| [#28](https://github.com/jmmana/sqlguardllm/issues/28) | Policy.from_database(): read tables and columns from a live database | S | [#5](https://github.com/jmmana/sqlguardllm/issues/5) |
| [#29](https://github.com/jmmana/sqlguardllm/issues/29) | Mask personal data instead of hiding it | M | [#5](https://github.com/jmmana/sqlguardllm/issues/5) |
| [#30](https://github.com/jmmana/sqlguardllm/issues/30) | Aggregate-only tables with a minimum group size (k-anonymity) | M | [#4](https://github.com/jmmana/sqlguardllm/issues/4) |

## [v0.4 · MCP server](https://github.com/jmmana/sqlguardllm/milestone/5)

A safe database tool for Claude Desktop, Cursor and any MCP client, with an optional cost guard.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#31](https://github.com/jmmana/sqlguardllm/issues/31) | MCP server: a safe database tool for any MCP client | L | [#12](https://github.com/jmmana/sqlguardllm/issues/12) [#27](https://github.com/jmmana/sqlguardllm/issues/27) [#28](https://github.com/jmmana/sqlguardllm/issues/28) |
| [#32](https://github.com/jmmana/sqlguardllm/issues/32) | MCP recipes for Claude Desktop, Cursor and VS Code 👋 | S | [#31](https://github.com/jmmana/sqlguardllm/issues/31) |
| [#33](https://github.com/jmmana/sqlguardllm/issues/33) | Optional cost guard with EXPLAIN for PostgreSQL and MySQL | M | [#31](https://github.com/jmmana/sqlguardllm/issues/31) |

## [v0.5 · Ecosystem & playground](https://github.com/jmmana/sqlguardllm/milestone/3)

Integration recipes, the in-browser «try to break it» playground and a performance budget.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#17](https://github.com/jmmana/sqlguardllm/issues/17) | Integration recipes: generic wrapper, LangChain, LlamaIndex, Vanna and MCP | M | [#10](https://github.com/jmmana/sqlguardllm/issues/10) |
| [#18](https://github.com/jmmana/sqlguardllm/issues/18) | Playground: «Try to break it» in the browser with Pyodide | L | [#10](https://github.com/jmmana/sqlguardllm/issues/10) [#9](https://github.com/jmmana/sqlguardllm/issues/9) |
| [#19](https://github.com/jmmana/sqlguardllm/issues/19) | Performance budget: under 2 ms per check, measured in CI | S | [#5](https://github.com/jmmana/sqlguardllm/issues/5) [#10](https://github.com/jmmana/sqlguardllm/issues/10) |

## [Later · Node.js and .NET](https://github.com/jmmana/sqlguardllm/milestone/6)

One engine, many clients: server mode, official npm and NuGet clients, and the corpus as a cross-language conformance suite.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#34](https://github.com/jmmana/sqlguardllm/issues/34) | Server mode: sqlguardllm serve with an HTTP API and a Docker image | M | [#12](https://github.com/jmmana/sqlguardllm/issues/12) [#11](https://github.com/jmmana/sqlguardllm/issues/11) |
| [#35](https://github.com/jmmana/sqlguardllm/issues/35) | Official Node.js client on npm (TypeScript) | M | [#34](https://github.com/jmmana/sqlguardllm/issues/34) |
| [#36](https://github.com/jmmana/sqlguardllm/issues/36) | Official .NET client on NuGet | M | [#34](https://github.com/jmmana/sqlguardllm/issues/34) |
| [#37](https://github.com/jmmana/sqlguardllm/issues/37) | Cross-language conformance: the corpus through every client | M | [#35](https://github.com/jmmana/sqlguardllm/issues/35) [#36](https://github.com/jmmana/sqlguardllm/issues/36) |
| [#38](https://github.com/jmmana/sqlguardllm/issues/38) | Spike: run the engine as WebAssembly in Node.js | S | — |

👋 = good first issue.
