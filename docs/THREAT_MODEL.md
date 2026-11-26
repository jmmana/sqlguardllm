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
| **Parser differentials** | sqlglot may understand a statement differently from the real engine | sqlguardllm regenerates the SQL from the validated tree, rejects unsupported syntax, and the corpus and property-based fuzz suite are exercised against real engines. Report any differential as a vulnerability |
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

The baseline attacks and allowed queries run in all ten supported dialects (`tests/test_dialect_matrix.py`), and the rewritten SQL runs on PostgreSQL 16, MySQL 8, SQL Server 2022, DuckDB and SQLite (`tests/engines/`, `.github/workflows/engines.yml`).

## Fuzzing oracle and limits

`tests/fuzz/` generates bounded SQLGlot query trees over the demonstration tables (`invoices`,
`invoice_lines`, `branches`, `customers` and `products`) and executes only SQL accepted by the
guard against SQLite, DuckDB, PostgreSQL 16, MySQL 8 and SQL Server 2022. The fixtures put unique
`CANARY_…` sentinels in every hidden or masked field and in the public fields of rows belonging to
other tenants. Any sentinel returned by an allowed query is a failure; the suite also checks the
row cap, fail-closed handling of guard exceptions, and rewrite idempotence. Hypothesis shrinks
failures, which are persisted in `tests/corpus/fuzz_regressions.yaml`, re-executed by the fuzz suite
against the engines, and included in generated conformance cases so all clients replay them.

Each run prints its seed, profile, engine set and configured examples per engine. Replay a run with
`SQLGUARD_FUZZ_SEED=<seed> SQLGUARD_FUZZ_EXAMPLES=<count> SQLGUARD_FUZZ_ENGINES=<engines> uv run pytest tests/fuzz`.

The oracle is limited to the rows, columns, query shapes, engine versions and policy fixture it
generates. It does not prove that every SQL construct or database configuration is safe, detect
inference through aggregates over otherwise allowed data, model timing or resource side channels,
or replace least-privilege database credentials and engine-specific security testing.
