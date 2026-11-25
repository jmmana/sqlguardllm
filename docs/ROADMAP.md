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
| [#5](https://github.com/jmmana/sqlguardllm/issues/5) | Rule 5: hidden columns in every clause and through stars | M | [#4](https://github.com/jmmana/sqlguardllm/issues/4) |
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
| [#13](https://github.com/jmmana/sqlguardllm/issues/13) | Dialect test matrix against real database engines | L | [#10](https://github.com/jmmana/sqlguardllm/issues/10) [#7](https://github.com/jmmana/sqlguardllm/issues/7) [#5](https://github.com/jmmana/sqlguardllm/issues/5) [#6](https://github.com/jmmana/sqlguardllm/issues/6) |
| [#14](https://github.com/jmmana/sqlguardllm/issues/14) | Documentation site with API reference | M | [#12](https://github.com/jmmana/sqlguardllm/issues/12) |
| [#15](https://github.com/jmmana/sqlguardllm/issues/15) | Release automation: PyPI Trusted Publishing and changelog | S | [#1](https://github.com/jmmana/sqlguardllm/issues/1) [#12](https://github.com/jmmana/sqlguardllm/issues/12) |
| [#16](https://github.com/jmmana/sqlguardllm/issues/16) | Extend denied functions for Snowflake, BigQuery, ClickHouse and Trino 👋 | S | [#6](https://github.com/jmmana/sqlguardllm/issues/6) |

## [v0.3 · Ecosystem & playground](https://github.com/jmmana/sqlguardllm/milestone/3)

Integration recipes, the in-browser «try to break it» playground and a performance budget.

| # | Issue | Size | Depends on |
|---|---|---|---|
| [#17](https://github.com/jmmana/sqlguardllm/issues/17) | Integration recipes: generic wrapper, LangChain, LlamaIndex, Vanna and MCP | M | [#10](https://github.com/jmmana/sqlguardllm/issues/10) |
| [#18](https://github.com/jmmana/sqlguardllm/issues/18) | Playground: «Try to break it» in the browser with Pyodide | L | [#10](https://github.com/jmmana/sqlguardllm/issues/10) [#9](https://github.com/jmmana/sqlguardllm/issues/9) |
| [#19](https://github.com/jmmana/sqlguardllm/issues/19) | Performance budget: under 2 ms per check, measured in CI | S | [#5](https://github.com/jmmana/sqlguardllm/issues/5) [#10](https://github.com/jmmana/sqlguardllm/issues/10) |

👋 = good first issue.
