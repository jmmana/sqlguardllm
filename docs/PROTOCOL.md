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
← {"id": 1, "type": "hello", "protocol": 1, "engine_version": "0.5.0a3", "dialects": ["bigquery", …]}
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
