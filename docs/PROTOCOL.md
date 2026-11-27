# Engine stdio protocol

`sqlguardllm engine --stdio` runs the embedded engine as a child process so any language package can drive it. It speaks **protocol version 1**: newline-delimited JSON, one request per line on stdin, one response per line on stdout. Same engine, same result shapes as the [HTTP server](SERVER.md).

- stdout carries only protocol messages; diagnostics go to stderr.
- The engine exits with code 0 on stdin EOF or after `shutdown`.
- A message is at most 1 MiB (UTF-8 bytes, newline included). Longer lines are skipped and answered with `message_too_large`.
- Messages are JSON objects on one line (no embedded raw newlines). Responses are ASCII-only JSON.
- SQL and context are never logged or echoed in errors.

The single JSON Schema is [`schema/protocol/protocol.schema.json`](https://github.com/jmmana/sqlguardllm/blob/main/schema/protocol/protocol.schema.json). `CheckResult` and `ExplainResult` are the bodies of HTTP `POST /v1/check` and `/v1/explain` and the `result` of the `check` and `explain` responses. The server tests, the protocol tests and the clients all validate against it.

## Requests

Every request has an `id` (string or integer) and a `type`; the response echoes the `id`. Requests are answered in order.

| `type` | Fields | Response |
|---|---|---|
| `hello` | | `protocol` (1), `engine_version`, `dialects` |
| `load_policy` | `policy` (object), or `policy_set` (object) with `role`; optional `language` (`en`, `es`) | `handle` |
| `check` | `handle`, `sql`, optional `context` | `result`: `CheckResult` |
| `explain` | `handle`, `sql`, optional `context` | `result`: `ExplainResult` |
| `shutdown` | | `{"id", "type": "shutdown"}`, then the engine exits |

```
→ {"id": 1, "type": "hello"}
← {"id": 1, "type": "hello", "protocol": 1, "engine_version": "0.5.0a7", "dialects": ["bigquery", …]}
→ {"id": 2, "type": "load_policy", "policy": {"dialect": "postgres", "tables": ["invoices"]}}
← {"id": 2, "type": "load_policy", "handle": "p1"}
→ {"id": 3, "type": "check", "handle": "p1", "sql": "SELECT id FROM invoices", "context": null}
← {"id": 3, "type": "check", "result": {"ok": true, "sql": "…", "tables": ["invoices"], …}}
```

A policy is parsed and compiled once by `load_policy`; the handle is reused by every later call. Handles live until the process ends (at most 256 per process).

A rejected query is a normal `check` response with `"ok": false`. Protocol failures use the error response.

## Errors

```json
{"id": 3, "type": "error", "allowed": false, "error": {"code": "unknown_handle", "message": "…"}}
```

`id` is `null` when the request had none. Every error carries `"allowed": false`: a client must treat anything other than a `check` response with `result.ok == true` as a rejection (fail closed).

| Code | Meaning |
|---|---|
| `malformed_message` | Not valid JSON, or not an object |
| `message_too_large` | Over 1 MiB |
| `invalid_request` | Missing or mistyped field (`id`, `sql`, `context`, `language`…) |
| `unknown_type` | Unknown `type` |
| `unknown_handle` | `handle` was never returned by `load_policy` |
| `invalid_policy` | The policy, policy set or role is invalid |
| `too_many_handles` | More than 256 policies loaded |
| `engine_error` | Any internal error |

## Compatibility rules

- `hello.protocol` is the major version of this protocol. Clients must refuse a version they do not know.
- Within version 1 changes are additive only: new optional request fields, new response fields, new message types, new error codes. Clients ignore unknown response fields and treat unknown error codes as rejections.
- Removing or changing the meaning of a field, a message type or an error code needs protocol version 2.
- Violation codes are stable across minor versions, as everywhere else in sqlguardllm.
- The engine rejects unknown request types with `unknown_type` and ignores unknown request fields.

## Standalone engine binaries

Packages for languages without Python ship the engine as a prebuilt executable that speaks this protocol (`<engine> --stdio`). It contains only sqlguardllm, sqlglot and the Python standard library (no `yaml`, `sqlalchemy`, `mcp` or OpenTelemetry), opens no network connection and never starts a shell. It is built with PyInstaller in one-dir mode by `scripts/build-engine.py`: a one-file build unpacks itself on every start and took about 4 s on macOS arm64, a one-dir build starts in about 0.1 s (`benchmarks/engine-binaries.json`).

Every GitHub Release `vX.Y.Z` has these assets, built from the release commit on one native runner each:

| Platform | Asset | Built on |
|---|---|---|
| Linux x64 (glibc) | `sqlguardllm-engine-linux-x64.tar.gz` | `ubuntu-24.04` |
| Linux arm64 (glibc) | `sqlguardllm-engine-linux-arm64.tar.gz` | `ubuntu-24.04-arm` |
| macOS arm64 | `sqlguardllm-engine-macos-arm64.tar.gz` | `macos-15` |
| macOS x64 | `sqlguardllm-engine-macos-x64.tar.gz` | `macos-15-intel` |
| Windows x64 | `sqlguardllm-engine-windows-x64.zip` | `windows-2022` |
| (all) | `SHA256SUMS` | |

Each archive holds one folder, `sqlguardllm-engine-<platform>/`, with the executable `sqlguardllm-engine` (`sqlguardllm-engine.exe` on Windows) and its `_internal/` files; keep them together. `SHA256SUMS` is the output of `sha256sum`, one line per archive, `<64 hex digits><two spaces><asset name>`, and lists exactly the five archives. Alpine and other musl Linux are not built: use the remote mode (`sqlguardllm serve`) there until a musl build is measured.

Binaries are not code-signed or notarised. On macOS a downloaded archive may carry the quarantine attribute; packages that unpack it themselves do not set it.

### Fetch and verify

```
python scripts/fetch-engine.py 0.5.0            # all five platforms into ./engines
python scripts/fetch-engine.py 0.5.0 --platform linux-x64 --out vendor/engine
```

The script (standard library only; this is what the Node, .NET and Java packaging jobs run) downloads `SHA256SUMS` and the archives of release `v0.5.0` and exits 1 on any failure: an asset missing from the release or from `SHA256SUMS`, a checksum mismatch, a failed `gh attestation verify` (run when `gh` is installed; `--require-attestation` also fails when it is not), a path outside the target folder in an archive, or, for the platform of the machine running it, an engine whose `hello` does not report exactly that version and protocol 1. It unpacks an archive only after it verified. Packaging jobs must not catch these failures.

By hand:

```
gh release download v0.5.0 -R jmmana/sqlguardllm -p 'sqlguardllm-engine-*' -p SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS        # macOS: shasum -a 256 -c
gh attestation verify sqlguardllm-engine-linux-x64.tar.gz -R jmmana/sqlguardllm
```

The attestation is a signed SLSA build provenance (`actions/attest-build-provenance`): it ties the archive to the `release.yml` run, the tag and the commit that produced it. A checksum alone only proves the download is intact; verify the attestation when the binary is about to be shipped or run in a sensitive place.

### Versions move in lockstep

The engine, the PyPI package and every language package share one version, checked by the release workflow (`pyproject.toml`, `clients/node/package.json` and the .csproj must agree with the tag, and each binary's `hello` must report the version of its commit). A client that launches an engine must send `hello` first and refuse to continue, treating every check as a rejection, unless:

- `protocol` equals the protocol version the client implements (1), and
- `engine_version` has the same major version as the client's own (before 1.0, the same minor version too: `0.5.x` clients accept `0.5.y` engines).

A packaging job that bundles the engine fetches exactly its own version. An engine with a different major or protocol version is never used, even if it answers.
