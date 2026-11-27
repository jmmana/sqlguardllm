using System.Data.Common;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;

namespace SqlGuardLlm.EntityFrameworkCore;

/// <summary>Settings of <see cref="SqlGuardCommandInterceptor"/>.</summary>
public sealed class SqlGuardInterceptorOptions
{
    /// <summary>
    /// Also check the SQL EF Core generates (LINQ queries, SaveChanges). Off by default: the guard is for SQL written by hand
    /// or by a model, and its read-only policy would reject EF's own INSERT/UPDATE. Raw SQL is always checked.
    /// </summary>
    public bool EnforceAll { get; set; }
    /// <summary>Where the context (user, tenant) comes from. It must not come from the model.</summary>
    public ISqlGuardContextProvider? Context { get; set; }
}

/// <summary>
/// EF Core interceptor that sends raw SQL commands (<c>FromSql</c>, <c>SqlQuery</c>, <c>ExecuteSql</c>) through sqlguardllm
/// and replaces the command text with the rewritten SQL before it reaches the database. A rejected command throws
/// <see cref="GuardViolationException"/>; a guard failure throws <see cref="SqlGuardException"/>: fail closed. The synchronous
/// paths block on the guard call. Command parameters are kept as they are.
/// </summary>
public sealed class SqlGuardCommandInterceptor : DbCommandInterceptor
{
    private readonly ISqlGuard _guard;
    private readonly SqlGuardInterceptorOptions _options;

    public SqlGuardCommandInterceptor(ISqlGuard guard, SqlGuardInterceptorOptions? options = null)
    {
        _guard = guard ?? throw new ArgumentNullException(nameof(guard));
        _options = options ?? new SqlGuardInterceptorOptions();
    }

    private bool Applies(CommandEventData e)
        => _options.EnforceAll || e.CommandSource is CommandSource.FromSqlQuery or CommandSource.ExecuteSqlRaw;

    private async ValueTask GuardAsync(DbCommand command, CommandEventData e, CancellationToken ct)
    {
        if (!Applies(e)) return;
        command.CommandText = await _guard.EnforceAsync(command.CommandText, _options.Context?.GetContext(), ct).ConfigureAwait(false);
    }

    private void Guard(DbCommand command, CommandEventData e)
        => GuardAsync(command, e, CancellationToken.None).AsTask().GetAwaiter().GetResult();

    public override InterceptionResult<DbDataReader> ReaderExecuting(DbCommand command, CommandEventData eventData, InterceptionResult<DbDataReader> result)
    { Guard(command, eventData); return result; }

    public override async ValueTask<InterceptionResult<DbDataReader>> ReaderExecutingAsync(DbCommand command, CommandEventData eventData, InterceptionResult<DbDataReader> result, CancellationToken cancellationToken = default)
    { await GuardAsync(command, eventData, cancellationToken).ConfigureAwait(false); return result; }

    public override InterceptionResult<int> NonQueryExecuting(DbCommand command, CommandEventData eventData, InterceptionResult<int> result)
    { Guard(command, eventData); return result; }

    public override async ValueTask<InterceptionResult<int>> NonQueryExecutingAsync(DbCommand command, CommandEventData eventData, InterceptionResult<int> result, CancellationToken cancellationToken = default)
    { await GuardAsync(command, eventData, cancellationToken).ConfigureAwait(false); return result; }

    public override InterceptionResult<object> ScalarExecuting(DbCommand command, CommandEventData eventData, InterceptionResult<object> result)
    { Guard(command, eventData); return result; }

    public override async ValueTask<InterceptionResult<object>> ScalarExecutingAsync(DbCommand command, CommandEventData eventData, InterceptionResult<object> result, CancellationToken cancellationToken = default)
    { await GuardAsync(command, eventData, cancellationToken).ConfigureAwait(false); return result; }
}

public static class SqlGuardDbContextOptionsExtensions
{
    /// <summary>Adds <see cref="SqlGuardCommandInterceptor"/> to the context options.</summary>
    public static DbContextOptionsBuilder UseSqlGuard(this DbContextOptionsBuilder builder, ISqlGuard guard, SqlGuardInterceptorOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(builder);
        return builder.AddInterceptors(new SqlGuardCommandInterceptor(guard, options));
    }
}
