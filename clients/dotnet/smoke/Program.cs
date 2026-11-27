// Smoke test of the packed NuGet package: starts the embedded engine (SHA-256 verified), allows one query, rejects one.
// Exit code 0 only if both behave. Used by dotnet-client.yml for `dotnet run`, `dotnet publish -r <rid>` (self-contained,
// single-file, Native AOT) and a Linux container image.
using SqlGuardLlm;

await using var guard = await SqlGuard.CreateLocalAsync(new SqlGuardLocalOptions
{
    PolicyJson = """{"dialect":"postgres","tables":["invoices"]}""",
});
var safe = await guard.EnforceAsync("SELECT id FROM invoices");
var bad = await guard.CheckAsync("DELETE FROM invoices");
Console.WriteLine($"safe={safe} rejected={!bad.Ok} codes={string.Join(",", bad.Violations.Select(v => v.Code))}");
return safe == "SELECT id FROM invoices LIMIT 1000" && !bad.Ok ? 0 : 1;
