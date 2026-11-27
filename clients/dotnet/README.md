# SqlGuardLlm for .NET

Official .NET client for [sqlguardllm](https://github.com/jmmana/sqlguardllm), the SQL firewall for LLM apps. It talks to `sqlguardllm serve` (the same engine, over HTTP) so .NET behaves exactly like Python. No runtime dependencies beyond `HttpClient` and `System.Text.Json`. Targets `net8.0` and `net10.0`.

> `SqlGuardClient` needs a running `sqlguardllm serve`; `SqlGuardEngine` runs the engine shipped in the package, with no Python and no server (see below).

```
dotnet add package SqlGuardLlm
```

## Usage

```csharp
using SqlGuardLlm;

using var guard = new SqlGuardClient("http://localhost:8080", token: Environment.GetEnvironmentVariable("SQLGUARD_TOKEN"));

// Context comes from YOUR app (the signed-in user), never from the model.
var context = new Dictionary<string, object?> { ["tenant_id"] = currentUser.TenantId };

CheckResult r = await guard.CheckAsync(sqlFromModel, context);
if (r.Ok) Run(r.Sql!);                 // rewritten, safe SQL
else foreach (var v in r.Violations) Console.WriteLine($"{v.Code}: {v.Message}");

string safe = await guard.EnforceAsync(sqlFromModel, context);   // throws GuardViolationException
```

With dependency injection, pass a factory-managed client: `new SqlGuardClient(httpClient, new SqlGuardOptions { BaseUrl = new("http://sqlguard:8080"), Token = token, Timeout = TimeSpan.FromSeconds(5) })`.

### Dapper / ADO.NET

```csharp
var safeSql = await guard.EnforceAsync(modelSql, new Dictionary<string, object?> { ["tenant_id"] = tenantId });

await using var conn = new NpgsqlConnection(readOnlyConnectionString);   // also use a read-only DB role
var rows = await conn.QueryAsync(safeSql);                               // Dapper
// or plain ADO.NET: using var cmd = conn.CreateCommand(); cmd.CommandText = safeSql; ...
```

## Embedded engine (no server)

The package carries the engine for linux-x64, linux-arm64, osx-x64, osx-arm64 and win-x64 under `runtimes/{rid}/native/sqlguardllm-engine/`:

```csharp
using var guard = await SqlGuardEngine.StartAsync(new { dialect = "postgres", tables = new[] { "invoices" } });
string safe = await guard.EnforceAsync(sqlFromModel, new Dictionary<string, object?> { ["tenant_id"] = tenantId });
```

Before it starts, the client recomputes the SHA-256 of every engine file against `engine.sha256` and refuses to run on any mismatch (`SqlGuardException`, code `engine_integrity`); it also refuses an engine whose version or protocol does not match the package. On an unsupported runtime (another OS or CPU, Alpine/musl) it throws `engine_unsupported`, whose message points to remote mode: use `SqlGuardClient` against `sqlguardllm serve`. The hashes ship in the same package as the binaries, so they catch corruption and tampering after install; the supply chain (checksums and attestations of the release) is verified by `scripts/fetch-engine.py` when the package is built. Unix permissions are not kept by NuGet, so the client sets the executable bit on first start: a read-only file system needs it set at image build time.

## API

| Member | Returns |
| --- | --- |
| `CheckAsync(sql, context?, ct)` | `CheckResult { Ok, Sql, Tables, AppliedFilters, Violations }` |
| `EnforceAsync(sql, context?, ct)` | the safe SQL, or throws `GuardViolationException` (has `Violations`) |
| `ExplainAsync(sql, context?, ct)` | `ExplainResult { Ok, Sql, Tables, Violations, Raw }` (`Raw` is the full `JsonElement`) |
| `HealthAsync(ct)` | `HealthResult { Status, Version }` |

## Fails closed

**If the server is unreachable, times out (default 5 s), returns an HTTP error or answers something unexpected, the client throws `SqlGuardException` (with a `Code`) and never returns an ok result.** Do not catch it and run the SQL anyway: a guard that lets queries through when it is down is not a guard. A *rejected* query is not an error: `CheckAsync` returns `Ok = false` with the violations.

Exception messages never include the token or the context. Cancelling through your own `CancellationToken` raises `OperationCanceledException`, not `SqlGuardException`.

## Security notes

- The `context` (tenant, user id...) must come from your app's authenticated session, never from the model or the user's prompt.
- Use TLS and a bearer token when the server is not on localhost or a private network.
- Run queries with a read-only database role as a second layer.

Apache-2.0.
