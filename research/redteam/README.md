# LLM red team

**Purpose: defensive testing to improve sqlguardllm.** The threat this library defends against is a language model, manipulated by a prompt, writing malicious SQL. So we use a language model to attack our own guard, against synthetic data, and fix what it finds before release. Its output is executed only inside the guard and on disposable test databases.

`run.py` asks Claude (`claude-opus-5-5`, adaptive thinking, structured outputs, server-side fallback on refusal) for attacks, sends **every** query through the guard and, for the allowed ones, runs the rewritten SQL on the canary databases of `tests/fuzz` (SQLite and DuckDB in process; PostgreSQL 16, MySQL 8 and SQL Server 2022 service containers in CI). The judge is the fuzzing oracle, imported from `tests/fuzz`, not copied: a canary in the output of an allowed query is a **bypass** (see "Fuzzing with canary data" in `docs/THREAT_MODEL.md`). Each query runs on the engine of its own dialect, once per role (`north`, `east`).

Generated SQL is executed **only** in the CI service containers (or the in-process engines) holding canary data, in read-only sessions with timeouts, never against any other database. Do not point the `SQLGUARD_*_URL` variables at anything else.

## Files

| File | What |
|---|---|
| `prompt.md` | The fixed system prompt (versioned; first paragraph states the defensive purpose). `prompt.sha256` holds its hash and a test fails if the prompt changes without it. |
| `run.py` | Generation, judging, `--rescore`. |
| `<date>/attacks.jsonl` | One line per generated query, allowed or rejected, duplicates included. |
| `<date>/manifest.json` | Totals, bypasses (at the top), token usage, cost, estimate, hashes. |
| `<date>/redteam_cases.yaml` | Only if there was a bypass: pending corpus cases. |

Each `attacks.jsonl` line: `index`, `model` (`response.model`), `prompt_sha256`, `policy_sha256`, `sqlguardllm_version`, `git_sha`, `sql`, `dialect`, `technique`, `target_invariant`, `rationale`, `fingerprint` (sha256 of the normalised SQL: literals replaced, case, spacing and comments dropped), `duplicate_of` (index of the first query with that fingerprint; duplicates are counted per technique, never dropped), `verdict` (role `north`: `allowed`, `violations`, `rewritten_sql`; `verdicts` has every role), `engines` (outcome per engine and role: `executed`, `rejected`, `engine_error`, `guard_only`, `not_applicable`, plus the oracle failures), `status` (`allowed`, `rejected`, `bypass`, `invariant_failure`) and `fix_release` (null until a bypass is fixed; fill it in when you commit the dataset).

## Cost model

Hard caps, all checked before anything is sent:

- `--max-attacks` (default and maximum 50) is the number of attacks requested.
- `--token-budget` (default 32,000, maximum 64,000) is the request's `max_tokens`: output and thinking tokens cannot pass it.
- `--max-usd` (default 2.00, maximum 5.00) is the per-run ceiling. The free `count_tokens` call gives the input size; the worst-case estimate is `reserved_input * USD 4/M + token_budget * USD 20/M`, where `reserved_input = max(count + max(1024, 25%), request bytes + 4096)` (the approach of `scripts/claude_issue_helper.py`). If the estimate is above the ceiling the run refuses to start (exit 1, `status: refused_by_budget`). The defaults estimate about USD 0.7.

The estimate is printed before the request and the actual cost (from `response.usage`) after. With the defaults a run costs well under USD 1. A refusal (`stop_reason: refusal`) is recorded as `status: refused` with its category and is not a failure (exit 0); a refused request is billed for the tokens it used. If the server-side fallback served the request, `fallback_used` is true and `model` is the model that answered.

Exit codes: 0 on success or refusal, 1 on a bypass, malformed output (nothing usable), a budget refusal or an API error.

## Running it

```bash
# free: count tokens and print the estimate
ANTHROPIC_API_KEY=... uv run python research/redteam/run.py --preflight-only --output-dir /tmp/rt
# one capped, billable request (a few engines are skipped when their SQLGUARD_*_URL is unset)
ANTHROPIC_API_KEY=... uv run python research/redteam/run.py --output-dir /tmp/rt
# re-judge a stored dataset with the current guard, no API call (paper, regression tests)
uv run python research/redteam/run.py --rescore research/redteam/<date>
```

In CI it is `.github/workflows/redteam.yml`: on manual dispatch only (no schedule), on `main` only, `contents: read`, the secret passed as `ANTHROPIC_API_KEY` to the generation step only, never pushing. The dataset is a workflow artifact.

## After a run

1. Download the artifact and review the dataset: model output must not reach the repository unreviewed.
2. Commit it as `research/redteam/<date>/`.
3. For each bypass: open a bug issue, triage the case in `redteam_cases.yaml` (`expect: reject` with `codes`, or the safe rewrite; remove `pending`), append it to `tests/corpus/redteam.yaml`, fix the guard and record the release in the attack's `fix_release`.
4. `--rescore` the dataset after guard changes: every stored attack must stay free of bypasses.

## Limits

One policy (the fuzzing policy: masking, hidden columns, row filters, `max_rows`) is judged; `aggregate_only` has no canary oracle yet. Attacks that run on one engine are not run on the others (SQL is dialect-specific). A clean run says only that these queries, on this data, broke nothing.
