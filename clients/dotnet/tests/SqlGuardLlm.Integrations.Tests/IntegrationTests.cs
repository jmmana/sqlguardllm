using System.Data.Common;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.SemanticKernel;
using SqlGuardExamples;
using SqlGuardLlm;
using SqlGuardLlm.EntityFrameworkCore;
using SqlGuardLlm.SemanticKernel;
using Xunit;

/// <summary>Stands in for the engine: rejects writes, appends a LIMIT to everything else, records what it saw.</summary>
internal sealed class FakeGuard : ISqlGuard
{
    public List<string> Seen { get; } = new();
    public List<IReadOnlyDictionary<string, object?>?> Contexts { get; } = new();
    public bool Down { get; set; }

    public Task<CheckResult> CheckAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        if (Down) throw new SqlGuardException("unreachable", "down");
        Seen.Add(sql); Contexts.Add(context);
        var write = new[] { "DROP", "DELETE", "UPDATE", "INSERT" }.Any(k => sql.Contains(k, StringComparison.OrdinalIgnoreCase));
        return Task.FromResult(write
            ? new CheckResult { Ok = false, Violations = [new Violation("write_operation", "no")] }
            : new CheckResult { Ok = true, Sql = sql + " LIMIT 10" });
    }
    public async Task<string> EnforceAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var r = await CheckAsync(sql, context, ct);
        return r.Ok ? r.Sql! : throw new GuardViolationException(r.Violations);
    }
    public Task<ExplainResult> ExplainAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default) => throw new NotSupportedException();
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}

public class IntegrationTests
{
    private static readonly Dictionary<string, object?> Ctx = new() { ["tenant_id"] = 7 };

    private static async Task<SqliteConnection> Db()
    {
        var c = new SqliteConnection("Data Source=:memory:");
        await c.OpenAsync();
        using var cmd = c.CreateCommand();
        cmd.CommandText = "CREATE TABLE invoices (Id INTEGER, TenantId INTEGER, Total REAL); INSERT INTO invoices VALUES (1,1,10),(2,1,20),(3,2,30);";
        cmd.ExecuteNonQuery();
        return c;
    }

    [Fact] public void AddSqlGuard_binds_options_from_configuration()
    {
        var cfg = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
        { ["SqlGuard:BaseUrl"] = "http://sg.test:9000", ["SqlGuard:Token"] = "tok", ["SqlGuard:Timeout"] = "00:00:02" }).Build();
        using var sp = new ServiceCollection().AddSqlGuard(cfg.GetSection("SqlGuard")).BuildServiceProvider();
        var o = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<SqlGuardOptions>>().Value;
        Assert.Equal(new Uri("http://sg.test:9000"), o.BaseUrl);
        Assert.Equal("tok", o.Token);
        Assert.Equal(TimeSpan.FromSeconds(2), o.Timeout);
        Assert.IsType<SqlGuardClient>(sp.GetRequiredService<ISqlGuard>());
    }

    [Fact] public void AddSqlGuard_rejects_a_non_positive_timeout()
    {
        using var sp = new ServiceCollection().AddSqlGuard(o => o.Timeout = TimeSpan.Zero).BuildServiceProvider();
        Assert.Throws<Microsoft.Extensions.Options.OptionsValidationException>(() => sp.GetRequiredService<ISqlGuard>());
    }

    [Fact] public void AddSqlGuard_accepts_a_local_guard_instance()
    {
        var g = new FakeGuard();
        using var sp = new ServiceCollection().AddSqlGuard(g).BuildServiceProvider();
        Assert.Same(g, sp.GetRequiredService<ISqlGuard>());
    }

    [Fact] public async Task Ado_runs_the_rewritten_sql_and_rejects_without_touching_the_db()
    {
        await using var c = await Db(); var g = new FakeGuard();
        await using (var r = await c.ExecuteGuardedReaderAsync(g, "SELECT Id FROM invoices", Ctx)) { Assert.True(await r.ReadAsync()); }
        Assert.Equal("SELECT Id FROM invoices", g.Seen[0]);
        Assert.Same(Ctx, g.Contexts[0]);
        using (var cmd = c.CreateCommand())
        {
            cmd.CommandText = "SELECT Id FROM invoices";
            await cmd.GuardAsync(g, Ctx);
            Assert.Equal("SELECT Id FROM invoices LIMIT 10", cmd.CommandText);
        }
        await Assert.ThrowsAsync<GuardViolationException>(() => c.CreateGuardedCommandAsync(g, "DELETE FROM invoices"));
        using var count = c.CreateCommand(); count.CommandText = "SELECT COUNT(*) FROM invoices";
        Assert.Equal(3L, count.ExecuteScalar());
    }

    [Fact] public async Task GuardAsync_refuses_stored_procedures()
    {
        var g = new FakeGuard();
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => new ProcCommand().GuardAsync(g));
        Assert.Equal("unsupported_command", e.Code);
        Assert.Empty(g.Seen);
    }

    private sealed class ProcCommand : DbCommand
    {
        public override string CommandText { get; set; } = "sp";
        public override int CommandTimeout { get; set; }
        public override System.Data.CommandType CommandType { get; set; } = System.Data.CommandType.StoredProcedure;
        public override bool DesignTimeVisible { get; set; }
        public override System.Data.UpdateRowSource UpdatedRowSource { get; set; }
        protected override DbConnection? DbConnection { get; set; }
        protected override DbParameterCollection DbParameterCollection => throw new NotSupportedException();
        protected override DbTransaction? DbTransaction { get; set; }
        public override void Cancel() { }
        public override int ExecuteNonQuery() => throw new NotSupportedException();
        public override object? ExecuteScalar() => throw new NotSupportedException();
        public override void Prepare() { }
        protected override DbParameter CreateDbParameter() => throw new NotSupportedException();
        protected override DbDataReader ExecuteDbDataReader(System.Data.CommandBehavior behavior) => throw new NotSupportedException();
    }

    [Fact] public async Task Dapper_helper_enforces()
    {
        await using var c = await Db(); var g = new FakeGuard();
        Assert.Equal(3, (await c.QueryGuardedAsync(g, "SELECT Id FROM invoices")).Count());
        await Assert.ThrowsAsync<GuardViolationException>(() => c.QueryGuardedAsync<int>(g, "UPDATE invoices SET Total = 0"));
        g.Down = true;
        await Assert.ThrowsAsync<SqlGuardException>(() => c.QueryGuardedAsync<int>(g, "SELECT Id FROM invoices"));
    }

    private static ShopContext Ef(SqliteConnection c, ISqlGuard g, bool all = false) => new(new DbContextOptionsBuilder<ShopContext>()
        .UseSqlite(c).UseSqlGuard(g, new SqlGuardInterceptorOptions { EnforceAll = all, Context = SqlGuardContextProvider.From(() => Ctx) }).Options);

    [Fact] public async Task Ef_checks_raw_sql_but_not_linq_by_default()
    {
        await using var c = await Db(); var g = new FakeGuard();
        await using var db = Ef(c, g);
        Assert.Equal(3, (await db.Invoices.FromSqlRaw("SELECT Id, TenantId, Total FROM invoices").ToListAsync()).Count);
        Assert.Single(g.Seen);
        Assert.Same(Ctx, g.Contexts[0]);
        Assert.Equal(3, await db.Invoices.CountAsync());
        Assert.Single(g.Seen); // LINQ not checked
    }

    [Fact] public async Task Ef_rejects_raw_writes_sync_and_async()
    {
        await using var c = await Db(); var g = new FakeGuard();
        await using var db = Ef(c, g);
        await Assert.ThrowsAsync<GuardViolationException>(() => db.Database.ExecuteSqlRawAsync("DELETE FROM invoices"));
        Assert.Throws<GuardViolationException>(() => db.Database.ExecuteSqlRaw("DELETE FROM invoices"));
        Assert.Throws<GuardViolationException>(() => db.Invoices.FromSqlRaw("DELETE FROM invoices RETURNING Id, TenantId, Total").ToList());
        await Assert.ThrowsAsync<SqlGuardException>(async () => { g.Down = true; await db.Invoices.FromSqlRaw("SELECT Id, TenantId, Total FROM invoices").ToListAsync(); });
    }

    [Fact] public async Task Ef_EnforceAll_checks_linq_too()
    {
        await using var c = await Db(); var g = new FakeGuard();
        await using var db = Ef(c, g, all: true);
        Assert.Equal(3, await db.Invoices.CountAsync());
        Assert.NotEmpty(g.Seen);
    }

    [Fact] public async Task AIFunction_runs_safe_sql_and_returns_violations_otherwise()
    {
        var g = new FakeGuard(); var ran = new List<string>();
        var f = new GuardedSqlFunction(g, (sql, _) => { ran.Add(sql); return Task.FromResult<object?>("rows"); }, SqlGuardContextProvider.From(() => Ctx));
        Assert.Equal("run_sql", f.Name);
        Assert.Equal("rows", await f.InvokeAsync(new AIFunctionArguments { ["sql"] = "SELECT 1" }));
        Assert.Equal(["SELECT 1 LIMIT 10"], ran);
        var bad = Assert.IsType<Dictionary<string, object?>>(await f.InvokeAsync(new AIFunctionArguments { ["sql"] = "DROP TABLE t" }));
        Assert.Equal(false, bad["ok"]); Assert.Single(ran);
        await Assert.ThrowsAsync<ArgumentException>(async () => await f.InvokeAsync(new AIFunctionArguments { ["sql"] = 5 }));
        g.Down = true;
        await Assert.ThrowsAsync<SqlGuardException>(async () => await f.InvokeAsync(new AIFunctionArguments { ["sql"] = "SELECT 1" }));
        Assert.Single(ran);
    }

    [Fact] public async Task SemanticKernel_filter_rewrites_rejects_and_fails_closed()
    {
        var g = new FakeGuard(); var ran = new List<string>();
        var k = new Kernel();
        k.FunctionInvocationFilters.Add(new SqlGuardFunctionFilter(g, new SqlGuardFilterOptions { Context = SqlGuardContextProvider.From(() => Ctx) }));
        var run = k.CreateFunctionFromMethod((string sql) => { ran.Add(sql); return "rows"; }, "run_sql");
        var other = k.CreateFunctionFromMethod((string text) => { ran.Add(text); return "x"; }, "other");

        Assert.Equal("rows", (await k.InvokeAsync(run, new KernelArguments { ["sql"] = "SELECT 1" })).GetValue<string>());
        Assert.Equal(["SELECT 1 LIMIT 10"], ran);
        var rej = (await k.InvokeAsync(run, new KernelArguments { ["sql"] = "DROP TABLE t" })).GetValue<string>();
        Assert.Contains("write_operation", rej); Assert.Single(ran);
        await k.InvokeAsync(other, new KernelArguments { ["text"] = "DROP me" }); // no sql argument: not checked
        Assert.Equal("DROP me", ran[1]); Assert.Equal(2, g.Seen.Count);
        g.Down = true;
        await Assert.ThrowsAsync<SqlGuardException>(() => k.InvokeAsync(run, new KernelArguments { ["sql"] = "SELECT 1" }));
        Assert.Equal(2, ran.Count);
    }

    [Fact] public async Task Examples_run_end_to_end()
    {
        var w = new StringWriter();
        await Demo.RunAsync(new FakeGuard(), w);
        var o = w.ToString();
        Assert.Contains("dapper: 3 rows", o); Assert.Contains("ado.net: 3", o);
        Assert.Contains("ef core: 3 rows", o); Assert.Contains("ef core rejected: write_operation", o);
        Assert.Contains("ai function: 3", o); Assert.Contains("write_operation", o);
        Assert.Contains("semantic kernel: 3", o);
    }
}
