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
