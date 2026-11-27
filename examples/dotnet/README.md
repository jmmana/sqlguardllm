# .NET examples

`SqlGuardExamples/Demo.cs` runs every .NET integration against an in-memory SQLite database: Dapper, ADO.NET, EF Core, a Microsoft.Extensions.AI function and a Semantic Kernel filter. `Program.cs` wires `ISqlGuard` with `services.AddSqlGuard(configuration.GetSection("SqlGuard"))` and needs `sqlguardllm serve` (`SqlGuard__BaseUrl`, `SqlGuard__Token`).

```
sqlguardllm serve --policy examples/policy.yaml &
dotnet run --project examples/dotnet/SqlGuardExamples
```

The example is built and exercised in CI by `clients/dotnet/tests/SqlGuardLlm.Integrations.Tests` (`dotnet test clients/dotnet/SqlGuardLlm.sln`), which runs `Demo.RunAsync` with a fake guard, so no server or network is needed.
