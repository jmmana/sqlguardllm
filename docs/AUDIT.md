# Audit log

*Español: [es/AUDIT.md](es/AUDIT.md)*

The guard can record **every decision**: who tried what, and why it was allowed or rejected. It is **off by default**. With no configuration nothing is built, written or timed, and behaviour and performance are unchanged.

Audit is deployment configuration, not policy, so it lives on `Guard`, the CLI, the HTTP server and the MCP server. It is not a key in policy files.

## Turn it on

```python
from sqlguardllm import AuditLog, Guard

guard = Guard(policy, audit=AuditLog("audit.jsonl"))   # JSON Lines, appended
guard = Guard(policy, audit=AuditLog("-"))             # JSON Lines on stderr
guard = Guard(policy, audit=AuditLog(logging.getLogger("myapp.audit")))
guard = Guard(policy, audit=lambda event: ship(event)) # any callable taking an AuditEvent
guard = Guard(policy, audit=AuditLog.otel())           # pip install "sqlguardllm[otel]"
```

For a policy set, `policies.guard("analyst", audit=...)` adds the role to every event.

From the command line:

```bash
sqlguardllm check --policy policy.yaml --audit-log audit.jsonl "SELECT …"
sqlguardllm serve --policy policy.yaml --audit-log audit.jsonl --audit-sql redacted
sqlguardllm mcp   --policy policy.yaml --db sqlite:///demo.db --audit-log -
```

`--audit-log PATH|-` writes to a file or, with `-`, to stderr (stdout carries the result or the MCP protocol). Without `--audit-log` nothing is logged, whatever `--audit-sql` says.

OpenTelemetry events are added to the current span as `sqlguardllm.decision`; outside a recording span OpenTelemetry drops them.

## The event

| Field | Meaning |
|---|---|
| `timestamp` | UTC time of the decision (ISO 8601) |
| `event_id` | unique id of the event |
| `version` | sqlguardllm version |
| `policy_fingerprint` | sha256 of the canonical policy: which policy decided |
| `role` | role of the policy set, or `null` |
| `allowed` | `true` or `false` |
| `violation_codes` | stable [violation codes](api.md); empty when allowed |
| `tables` | tables the query reads (as far as the guard got, when rejected) |
| `sql_fingerprint` | sha256 of the normalised SQL (literals replaced by `?`): the same for every query of the same shape |
| `duration_ms` | time the decision took |
| `context_keys` | **names** of the context keys, such as `user.branches`; values are never logged |
| `sql` | the SQL, only as `include_sql` says |

## SQL in the event: `include_sql`

| Value | Logs |
|---|---|
| `"redacted"` (default when audit is on, `--audit-sql redacted`) | the query with every literal replaced by `?` and comments dropped. If the SQL can't be parsed it can't be redacted, so `sql` is omitted |
| `"none"` | no SQL, only the fingerprint |
| `"raw"` | the SQL exactly as received. **It may contain personal data** (emails, ids, names typed by the model or the user). Turn it on only if you may store that |

Context values are never logged in any mode. The row filters injected into the query are not logged either.

## When the sink fails

A failing sink never turns a rejection into an allowance.

- Default: the failure is reported **once** on stderr (without SQL) and the decision stands.
- `AuditLog(..., strict=True)`: if the decision can't be recorded, an allowed query is **rejected** with the code `audit_failure`. Use it when an unrecorded query is unacceptable.

## Notes

- Events are written when `check()` (and so `enforce()`, `wrap()`, the server's `/v1/check` and the MCP `query` tool) decides. `explain()` and `explain_query` don't record.
- File sinks open the file in append mode for every event and one thread writes at a time; rotate it with your usual tools.
- The timing budget of [`benchmarks/`](https://github.com/jmmana/sqlguardllm/tree/main/benchmarks) (median under 2 ms per query) is checked with audit on.
