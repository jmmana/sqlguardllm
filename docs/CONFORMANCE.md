# Cross-language conformance

Python, the HTTP server and every official client must give exactly the same answer for every case in `tests/corpus/`.

- `tests/conformance/export_corpus.py` writes `conformance/cases.json`: the non-pending corpus cases grouped by policy (the corpus `standard_policy` plus its overrides), each with the in-process `Guard.check` answer (`ok`, `sql`, sorted violation `codes`).
- `tests/conformance/test_http.py` serves each group over HTTP and compares with that answer, and checks `cases.json` is up to date.
- `tests/conformance/test_stdio.py` runs each group through a real `sqlguardllm engine --stdio` child process ([PROTOCOL.md](PROTOCOL.md)) and compares with the same answer.
- `clients/node/test/conformance.test.ts` and `clients/dotnet/tests/SqlGuardLlm.Tests/ConformanceTests.cs` start a real `sqlguardllm serve` per group (port 0) and compare the client's result with the same expectations.
- The `Conformance` workflow runs all three with `SQLGUARD_REQUIRE_CONFORMANCE=1`.

After changing `tests/corpus/*.yaml` run `uv run python tests/conformance/export_corpus.py` and commit `conformance/cases.json`.

## Running locally

The client tests launch `uv run --project <repo> --extra serve sqlguardllm`. Override with `SQLGUARD_SERVER_CMD` (for example `sqlguardllm` or `python -m sqlguardllm`). If it cannot run, the tests skip with a warning; `SQLGUARD_REQUIRE_CONFORMANCE=1` makes that an error.

## Adding a client

1. Read `conformance/cases.json`: `{"groups": [{"name", "policy", "cases": [...]}]}`.
2. For each group write `policy` to a JSON file and start `sqlguardllm serve --policy FILE --port 0`; the port is on stderr (`listening on http://...`).
3. For each case call the client's `check(sql, context)` and assert `ok`, `sql` (exact) and the set of violation codes equal `expected`, and that `ok` matches `expect` (`allow`/`reject`).
4. Add the job to `.github/workflows/conformance.yml`.

Any disagreement is a bug in the client or the server, never in `expected`.
