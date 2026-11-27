# .NET, step by step

[Leer en español](es/guide-dotnet.md) · [Choose a package](packages.md) · [Python](guide-python.md) · [Node.js](guide-node.md)

The NuGet package `SqlGuardLlm` carries the engine for linux-x64, linux-arm64, osx-x64, osx-arm64 and win-x64 (under `runtimes/{rid}/native/`), so the guard runs as a child process of your app with **no Python and no server**. This is the local mode, and it is what this guide uses. You need the .NET 8 or .NET 10 SDK. If your platform has no engine (another OS or CPU, or musl Linux such as Alpine), use the [remote mode](#remote-mode-a-server-instead-of-the-embedded-engine) with a running server.

## 1. Create the project and write a policy

```bash
dotnet new console -n GuardDemo
cd GuardDemo
dotnet add package SqlGuardLlm --prerelease
```

`--prerelease` is needed while the package is in alpha. The package is on [NuGet](https://www.nuget.org/packages/SqlGuardLlm).

Save this as `policy.json` in the `GuardDemo` folder:

```json
{
  "dialect": "postgres",
  "tables": ["invoices", "branches", "customers"],
  "hidden_columns": ["customers.email"],
  "row_filters": {
    "invoices": "branch_id IN ({user.branches})",
    "branches": "id IN ({user.branches})"
  },
  "max_rows": 100
}
```

The model may read three tables ([`tables`](policy-reference.md#tables)), never `customers.email` ([`hidden_columns`](policy-reference.md#hidden_columns)), only the invoices and branches of the user's branches ([`row_filters`](policy-reference.md#row_filters)), and at most 100 rows ([`max_rows`](policy-reference.md#max_rows)).

## 2. Check one allowed and one rejected query

Replace `Program.cs` with:

```csharp
using SqlGuardLlm;

// Starts the embedded engine (the SHA-256 of its files is verified first) and loads the policy.
await using var guard = await SqlGuard.CreateLocalAsync(new SqlGuardLocalOptions
{
    PolicyJson = await File.ReadAllTextAsync("policy.json"),
});

// The manager of branch 1. In your app this comes from the signed-in user, never from the model.
var context = new Dictionary<string, object?>
{
    ["user"] = new Dictionary<string, object?> { ["branches"] = new[] { 1 } },
};

var invoices = await guard.CheckAsync("SELECT id, total FROM invoices ORDER BY id", context);
Console.WriteLine($"{invoices.Ok} {invoices.Sql}");

var emails = await guard.CheckAsync("SELECT name, email FROM customers", context);
Console.WriteLine($"{emails.Ok} {string.Join("; ", emails.Violations.Select(v => $"{v.Code}: {v.Message}"))}");
```

`SqlGuard.CreateLocalAsync` returns an `ISqlGuard`, the same interface (`CheckAsync`, `EnforceAsync`, `ExplainAsync`, each with a `CancellationToken`) as the remote `SqlGuardClient`. It is thread-safe: share one instance for the whole app.

## 3. Run it

```bash
dotnet run
```

Expected output:

```text
True SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
False hidden_column: customers.email is a hidden column
```

The first query is allowed and rewritten: only branch 1's invoices, with a `LIMIT`. Run `invoices.Sql`, never the original text, with a read-only database user. The second is rejected because the policy hides `customers.email`. Set `Language = "es"` in `SqlGuardLocalOptions` for messages in Spanish.

The client **fails closed**: if the engine cannot start, a file does not match its SHA-256, the engine crashes or a call times out, the call throws a `SqlGuardException` (and the engine is restarted with a backoff) instead of letting the query through, so never catch that exception and run the SQL anyway. On an unsupported platform the exception has code `engine_unsupported` and its message names the remote mode.

It also works with `dotnet publish -r <rid>`, self-contained and single-file publishes (the `sqlguardllm-engine` folder sits next to the executable), Native AOT, and Linux container images (glibc based, such as `mcr.microsoft.com/dotnet/runtime-deps`; not Alpine).

## 4. Change a rule

To let the model read the email masked, replace the `hidden_columns` line of `policy.json` with:

```json
  "masked_columns": {"customers.email": "partial"},
```

Run `dotnet run` again. The second line becomes `True` with no violations: the query is allowed, and `ceo@acme.example` comes back as `ce***` ([`masked_columns`](policy-reference.md#masked_columns)). You can flip these rules without installing anything in the [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Remote mode: a server instead of the embedded engine

Use it when several services share one policy, or on a platform that has no embedded engine. The engine then runs in `sqlguardllm serve`, an HTTP server next to your application (Python 3.10 or later). In another terminal, from the same folder, with [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787
```

With [pipx](https://pipx.pypa.io/) it is `pipx run sqlguardllm serve --policy policy.json --port 8787`. With plain pip: `pip install --pre sqlguardllm`, then `sqlguardllm serve --policy policy.json --port 8787`. It only listens on your machine; before exposing it to other hosts, set a [token](SERVER.md#token). In `Program.cs`, replace the `await using var guard = ...` statement with:

```csharp
using var guard = new SqlGuardClient("http://localhost:8787");
```

The output is the same. If you see `SqlGuardException: Could not reach the sqlguardllm server.`, the server is not running; the client fails closed there too. Start the server with `--language es` for messages in Spanish. After editing `policy.json`, restart the server.

## Next

- [Client README](../clients/dotnet/README.md): local and remote mode, `EnforceAsync`, dependency injection, Dapper and ADO.NET, tokens and errors.
- [Recipes](RECIPES.md#net): `AddSqlGuard`, EF Core, Dapper, Semantic Kernel and Microsoft.Extensions.AI.
- [HTTP server](SERVER.md): flags, the `/v1` contract and security notes.
- [Policy reference](policy-reference.md): every field.
