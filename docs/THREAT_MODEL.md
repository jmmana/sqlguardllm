# Threat model

## What we protect

The confidentiality and integrity of a database that an application exposes to SQL written by an untrusted source: an LLM, an agent, a plugin or an end user.

## The attacker

We assume the attacker **fully controls the SQL string**. That is the realistic worst case for LLM apps: through prompt injection (in the question, in documents, or in rows of the database itself) or plain model error, any SQL can come out of the model.

The attacker does **not** control the `Policy`, the `context` passed to `check()`, or the database credentials. Those come from the application.

## What sqlguardllm guarantees

If `check()` returns `ok`, the returned SQL:

1. Is a single query whose syntax tree contains no write, DDL, locking or session-changing node.
2. Reads only allowlisted tables, never a hidden column (directly, through `*`, or in a predicate), and calls no denied function.
3. Sees only the rows allowed by the row filters, bound to the values in `context`.
4. Returns at most `max_rows` rows.

And if anything can't be understood, `check()` rejects it.

## What it does not protect against

| Risk | Why it's out of scope | What to do |
|---|---|---|
| **Missing database permissions** | sqlguardllm is a layer above the database, not a replacement | Always connect with a read-only user limited to the needed tables |
| **Expensive queries** | Cost depends on data and plans, not on syntax | Statement timeouts, a read replica, resource limits per user |
| **Parser differentials** | sqlglot may understand a statement differently from the real engine | sqlguardllm regenerates the SQL from the validated tree, rejects unsupported syntax, and the corpus is run against real engines (v0.2). Report any differential as a vulnerability |
| **Inference through allowed data** | Aggregates and filters over allowed columns can reveal facts about hidden ones if they are correlated | Hide correlated columns too, or expose a view without them |
| **Side effects of allowed functions** | Any function may have side effects, especially user-defined ones | Use `function_policy="allowlist"` when your database has UDFs |
| **Timing side channels** | Out of scope for a syntax-level guard | Timeouts, rate limits |
| **A wrong policy** | sqlguardllm enforces the policy it is given | Review policies like code; test them with the CLI |
| **Prompt injection itself** | sqlguardllm doesn't read natural language | It makes a successful injection harmless *for data access*; other effects (wrong answers) remain |

## Assumptions

- The sqlglot version in use is within the tested range.
- The application passes the **authenticated** user's attributes in `context`, never values taken from the model's output.
- The `Policy` is defined by the application owner, not built from user input.

## Reporting

A way to make `check()` return `ok` for SQL that breaks one of the four guarantees above is a security vulnerability. Report it privately: [SECURITY.md](../SECURITY.md).

## Parser differentials found

Places where sqlglot's tree did not say what the real engine would do. Each was fixed and has a corpus case.

| Found | What happened | Fix |
|---|---|---|
| Unqualified columns in `HAVING`, `QUALIFY` and expressions of `ORDER BY` / `DISTINCT ON` (0.2.0a2) | sqlglot leaves them out of `Scope.columns` because they could be select aliases, so `HAVING MAX(email) > 'm'` skipped the hidden-column rule | `scope_columns()` adds them back and checks them as source columns |
| `TOP n PERCENT`, `FETCH FIRST n PERCENT` and `WITH TIES` (0.3.0a1) | sqlglot keeps the flags in `limit_options`; the limit rule looked elsewhere, so `max_rows` was bypassed | Any inexact limit is replaced with a plain limit of `max_rows` |
| Column names in another case on MySQL (unreleased, found by the fuzzer) | MySQL ignores the case of column names, even quoted, but sqlglot's MySQL dialect is case-sensitive (MySQL table names can be), so `SELECT EMAIL FROM customers` skipped the hidden-column rule and `PHONE` skipped masking | Hidden and masked columns are matched case-insensitively in every dialect, which can only reject or mask more |

The baseline attacks and allowed queries run in all ten supported dialects (`tests/test_dialect_matrix.py`), and the rewritten SQL runs on PostgreSQL 16, MySQL 8, SQL Server 2022, DuckDB and SQLite (`tests/engines/`, `.github/workflows/engines.yml`).

## Fuzzing with canary data

The corpus holds the attacks someone thought of. `tests/fuzz/` searches for the ones nobody did: a `hypothesis` strategy writes queries over the demo schema (joins, `USING` and `NATURAL` joins, derived tables, CTEs and recursive CTEs, set operations, `GROUP BY`/`HAVING`, `QUALIFY`, window functions, `DISTINCT ON`, `ORDER BY` over aliases and expressions, `LIMIT`/`FETCH`/`TOP` with `PERCENT` and `WITH TIES`, correlated subqueries, other case and quoting of names, keyword case, whitespace and comments), the guard checks each one, and every allowed query is executed.

**The oracle is the data, not the guard.** The policy hides two columns, masks three, filters three tables by branch and caps results at 7 rows. The data lives in two worlds that agree on everything a role may see and differ in everything it may not:

- Every row of a filtered table carries `CANARY_T<branch>_…`, every hidden value contains `canary_hidden` and every masked value `canary_masked`. One of them in any output cell, whatever the query did to put it there, is a leak.
- World B changes every secret (hidden and masked values, sorted the other way round, and every value of branch 4, which no role is given) but has the same rows in the same order, so the engine plans the query the same way. An allowed query must return exactly the same rows in both worlds. If it doesn't, a secret reached the output without being copied into it: a comparison in `HAVING`, an `ORDER BY` that the row limit cuts, a `SUM` over another branch. With the fixes for the two bypasses above reverted, the oracle reports both (`test_oracle_catches_past_bypasses`), and the fuzzer finds them within about 2,000 examples.

Every example is also checked for `max_rows`, for `check()` never raising (and never rejecting without a reason), and for idempotence: the guard accepts its own output, and checking it again changes nothing, or, where a row filter or a mask is applied again (by design), returns the same rows.

A pull request runs 300 examples on SQLite and DuckDB with a fixed seed (a few seconds); every night `.github/workflows/engines.yml` runs 20,000 with a new seed on PostgreSQL 16, MySQL 8, SQL Server 2022, DuckDB and SQLite. The seed and the count are printed, so any run replays exactly, and a failure is shrunk to a short query and appended to `tests/corpus/fuzz_regressions.yaml`, which the corpus test, the fuzz regression test and the conformance clients replay from then on. Statistics of a run are in `research/results/fuzz.json`.

### What it cannot prove

Fuzzing is testing, not a proof. A clean run says that these queries, on this data, broke nothing.

- **Only what the generator writes.** It writes SQL through sqlglot, so syntax sqlglot can't produce (most vendor extensions) is only reached through the text mangling, and only one schema and one policy are used. Hidden columns on a row-filtered table, `aggregate_only`, `required_filters`, the `hash` mask, function allowlists and `output_dialect` are not exercised.
- **Only what the two worlds tell apart.** A value copied after a transformation (`SUBSTR`, arithmetic) carries no canary, and a leak whose result happens to be the same in both worlds (the nullness of a masked column, the two characters `partial` shows by design, the number of rows in branch 4, which is the same in both) is not seen.
- **Only two roles.** Contexts of other shapes (empty lists, strings, several placeholders) are not generated.
- **No side channels.** Timing and error messages are not compared, except a query that fails in one world only.
- **Plans can differ between two SQL texts.** For the idempotence check, a result cut by a limit or depending on `ROW_NUMBER` ties is compared by row count only.
- **Engines have bugs too.** DuckDB 2.0.1 ignores the `LIMIT` of a query with a `LEFT JOIN` whose right side its statistics prove empty (`SELECT c.id FROM customers c LEFT JOIN customers t ON t.name = 'a' LIMIT 7` returns every row), so on DuckDB `max_rows` holds only as far as the engine honours `LIMIT`. The guard's SQL is right; the fuzzer counts these results (`engine_ignored_limit`) instead of failing on them, on DuckDB only.
- **Idempotence is not literal.** Checking the guard's output again applies the row filters again (a nested derived table) and moves comments; and with a hidden column on a row-filtered table the guard rejects its own output, because the filter's `SELECT *` reads the hidden column. That fails closed, so the fuzzing policy keeps hidden columns on unfiltered tables.
