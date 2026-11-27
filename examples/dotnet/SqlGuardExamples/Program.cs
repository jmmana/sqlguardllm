using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using SqlGuardExamples;
using SqlGuardLlm;

// Needs `sqlguardllm serve` (see docs/SERVER.md). Options are bound from configuration: SqlGuard:BaseUrl, SqlGuard:Token.
// Environment variables SqlGuard__BaseUrl and SqlGuard__Token work too.
var configuration = new ConfigurationBuilder()
    .AddInMemoryCollection(new Dictionary<string, string?> { ["SqlGuard:BaseUrl"] = "http://localhost:8080" })
    .AddEnvironmentVariables()
    .Build();

var services = new ServiceCollection();
services.AddSqlGuard(configuration.GetSection("SqlGuard"));
await using var provider = services.BuildServiceProvider();

await Demo.RunAsync(provider.GetRequiredService<ISqlGuard>(), Console.Out);
