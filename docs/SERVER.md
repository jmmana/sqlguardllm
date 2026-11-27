# Server mode

`sqlguardllm serve` runs the same engine as an HTTP sidecar, so any language can use it (Node, .NET, Go...). It uses only the Python standard library.

```bash
pip install "sqlguardllm[serve]"
sqlguardllm serve --policy policy.yaml --port 8787
```

The extra only adds PyYAML, so the server can read YAML policy files.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--policy FILE` | required | A policy file, or a policy-set file (use `--role`) |
| `--role NAME` | none | Role to use from a policy-set file |
| `--host` | `127.0.0.1` | Address to bind |
| `--port` | `8787` | Port (0 picks a free one) |
| `--language en\|es` | `en` | Language of violation messages |
| `--token-env NAME` | none | Name of an environment variable holding a bearer token |

An invalid policy or an empty `--token-env` variable exits with code 2. The token is never read from the command line.

## Contract

See the [package guide](packages.md#http-protocol-version) for the protocol compatibility policy.

All responses are `application/json; charset=utf-8`.

- `GET /v1/health` returns `{"status": "ok", "version": "..."}`. It never needs a token.
- `POST /v1/check` with `{"sql": "...", "context": {...} | null}` returns 200 with
  `{"ok": bool, "sql": str|null, "tables": [...sorted], "applied_filters": [...], "violations": [{"code", "message", "detail"}]}`,
  the same JSON as `sqlguardllm check --format json`. A rejected query is still 200 with `"ok": false`.
- `POST /v1/explain` takes the same body and returns `Guard.explain(...).to_dict()`.

Errors are `{"error": {"code": "...", "message": "..."}}`: 400 `bad_request` (invalid JSON or wrong types), 401 `unauthorized`, 404 `not_found`, 405 `method_not_allowed`, 413 `payload_too_large` (body over 1 MiB). Errors and logs never include the SQL, the context or the token; the server writes no access log.

```bash
curl -s localhost:8787/v1/check -d '{"sql": "SELECT id FROM invoices", "context": {"user": {"branches": [1]}}}'
```

## Token

```bash
export SQLGUARD_TOKEN=$(openssl rand -hex 32)
sqlguardllm serve --policy policy.yaml --token-env SQLGUARD_TOKEN
curl -H "Authorization: Bearer $SQLGUARD_TOKEN" ...
```

Every request except `/v1/health` then needs the header (compared in constant time); otherwise 401.

## Audit log

Off by default. `--audit-log PATH|-` records every `/v1/check` decision as JSON Lines in a file or, with `-`, on stderr; `--audit-sql {none,redacted,raw}` says how much SQL the events carry (default `redacted`: literals replaced by `?`). With `--role` the events carry the role. Context values are never logged. See [Audit log](AUDIT.md).

```bash
sqlguardllm serve --policy policy.yaml --audit-log /var/log/sqlguard/audit.jsonl
```

## Docker

```bash
docker build -t sqlguardllm-serve .
docker run -p 8787:8787 -v ./examples:/policy:ro -e SQLGUARD_TOKEN \
  sqlguardllm-serve --policy /policy/policy.yaml --token-env SQLGUARD_TOKEN
```

The image runs as a non-root user, binds `0.0.0.0` inside the container and has a health check on `/v1/health`.

## Security notes

- Bind to loopback (the default) or put the server behind your network controls (private network, service mesh, firewall). There is no TLS; terminate it in front if traffic leaves the host. With `--host 0.0.0.0` always set a token.
- The `context` (row-filter values such as the user's branches) must come from your application's authenticated session, never from the model's output. Anyone who can call the server can pass any context.
- The server only checks and rewrites SQL; it never connects to a database. Execute the returned SQL with a read-only database user.
