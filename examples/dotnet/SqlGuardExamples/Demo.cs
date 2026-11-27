using System.Text.Json;
using Dapper;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.AI;
using Microsoft.SemanticKernel;
using SqlGuardLlm;
using SqlGuardLlm.EntityFrameworkCore;
using SqlGuardLlm.SemanticKernel;

namespace SqlGuardExamples;

public sealed class Invoice
{
    public int Id { get; set; }
    public int TenantId { get; set; }
    public double Total { get; set; }
}

public sealed class ShopContext(DbContextOptions<ShopContext> options) : DbContext(options)
{
    public DbSet<Invoice> Invoices => Set<Invoice>();
    protected override void OnModelCreating(ModelBuilder b) => b.Entity<Invoice>().ToTable("invoices");
}

/// <summary>Each integration against an in-memory SQLite database. Works with any <see cref="ISqlGuard"/>: remote, local or a test double.</summary>
public static class Demo
{
    // Context comes from the signed-in user in a real app, never from the model.
    private static readonly IReadOnlyDictionary<string, object?> Tenant = new Dictionary<string, object?> { ["tenant_id"] = 1 };
    private static readonly ISqlGuardContextProvider TenantContext = SqlGuardContextProvider.From(() => Tenant);

    public static async Task RunAsync(ISqlGuard guard, TextWriter output)
    {
        await using var conn = new SqliteConnection("Data Source=:memory:");
        await conn.OpenAsync();
        await conn.ExecuteAsync("CREATE TABLE invoices (Id INTEGER, TenantId INTEGER, Total REAL); INSERT INTO invoices VALUES (1, 1, 10.5), (2, 1, 20), (3, 2, 99);");

        // 1. Dapper: the SQL is checked first; the database never sees a rejected query.
        var rows = await conn.QueryGuardedAsync<Invoice>(guard, "SELECT Id, TenantId, Total FROM invoices", context: Tenant);
        output.WriteLine($"dapper: {rows.Count()} rows");

        // 2. ADO.NET
        await using (var reader = await conn.ExecuteGuardedReaderAsync(guard, "SELECT COUNT(*) FROM invoices", Tenant))
        {
            await reader.ReadAsync();
            output.WriteLine($"ado.net: {reader.GetInt64(0)}");
        }

        // 3. EF Core: the interceptor checks raw SQL only.
        var options = new DbContextOptionsBuilder<ShopContext>()
            .UseSqlite(conn)
            .UseSqlGuard(guard, new SqlGuardInterceptorOptions { Context = TenantContext })
            .Options;
        await using (var db = new ShopContext(options))
        {
            var viaEf = await db.Invoices.FromSqlRaw("SELECT Id, TenantId, Total FROM invoices").ToListAsync();
            output.WriteLine($"ef core: {viaEf.Count} rows");
            try { await db.Database.ExecuteSqlRawAsync("DELETE FROM invoices"); }
            catch (GuardViolationException e) { output.WriteLine($"ef core rejected: {e.Violations[0].Code}"); }
        }

        // 4. Microsoft.Extensions.AI: a tool the model calls; a rejection goes back to the model.
        var tool = new GuardedSqlFunction(guard, async (sql, ct) => (await conn.QueryAsync(new CommandDefinition(sql, cancellationToken: ct))).Count(), TenantContext);
        var ok = await tool.InvokeAsync(new AIFunctionArguments { ["sql"] = "SELECT Id FROM invoices" });
        var bad = await tool.InvokeAsync(new AIFunctionArguments { ["sql"] = "DROP TABLE invoices" });
        output.WriteLine($"ai function: {ok}");
        output.WriteLine($"ai function rejected: {JsonSerializer.Serialize(bad)}");

        // 5. Semantic Kernel: the filter checks the "sql" argument of any function.
        var kernel = new Kernel();
        kernel.FunctionInvocationFilters.Add(new SqlGuardFunctionFilter(guard, new SqlGuardFilterOptions { Context = TenantContext }));
        var query = kernel.CreateFunctionFromMethod(
            async (string sql) => (await conn.QueryAsync(sql)).Count(), "run_sql");
        var skOk = await kernel.InvokeAsync(query, new KernelArguments { ["sql"] = "SELECT Id FROM invoices" });
        var skBad = await kernel.InvokeAsync(query, new KernelArguments { ["sql"] = "DROP TABLE invoices" });
        output.WriteLine($"semantic kernel: {skOk.GetValue<int>()}");
        output.WriteLine($"semantic kernel rejected: {skBad.GetValue<string>()}");
    }
}
