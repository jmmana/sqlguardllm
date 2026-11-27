using System.Data;
using System.Data.Common;
using Dapper;

namespace SqlGuardLlm;

/// <summary>
/// ADO.NET and Dapper helpers: the SQL is checked (and rewritten) by the guard first, and the call fails with
/// <see cref="GuardViolationException"/> or <see cref="SqlGuardException"/> without touching the database.
/// Also use a read-only database role as a second layer.
/// </summary>
public static class GuardedDb
{
    /// <summary>Checks <paramref name="command"/>'s text and replaces it with the rewritten SQL. Only <see cref="CommandType.Text"/> is allowed.</summary>
    public static async Task<DbCommand> GuardAsync(this DbCommand command, ISqlGuard guard, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(command);
        ArgumentNullException.ThrowIfNull(guard);
        if (command.CommandType != CommandType.Text)
            throw new SqlGuardException("unsupported_command", "Only text commands can be checked; stored procedures are not allowed.");
        command.CommandText = await guard.EnforceAsync(command.CommandText, context, ct).ConfigureAwait(false);
        return command;
    }

    /// <summary>Creates a command whose text is the guard-approved SQL. Add parameters afterwards as usual.</summary>
    public static async Task<DbCommand> CreateGuardedCommandAsync(this DbConnection connection, ISqlGuard guard, string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(connection);
        ArgumentNullException.ThrowIfNull(guard);
        var safe = await guard.EnforceAsync(sql, context, ct).ConfigureAwait(false);
        var cmd = connection.CreateCommand();
        cmd.CommandText = safe;
        return cmd;
    }

    /// <summary>Checks the SQL and runs it. The caller disposes the reader; the command is released with it.</summary>
    public static async Task<DbDataReader> ExecuteGuardedReaderAsync(this DbConnection connection, ISqlGuard guard, string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var cmd = await connection.CreateGuardedCommandAsync(guard, sql, context, ct).ConfigureAwait(false);
        try { return await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false); }
        catch { await cmd.DisposeAsync().ConfigureAwait(false); throw; }
    }

    /// <summary>Dapper: checks the SQL, then <c>QueryAsync&lt;T&gt;</c> on the rewritten text.</summary>
    public static async Task<IEnumerable<T>> QueryGuardedAsync<T>(this IDbConnection connection, ISqlGuard guard, string sql, object? param = null,
        IReadOnlyDictionary<string, object?>? context = null, IDbTransaction? transaction = null, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(connection);
        ArgumentNullException.ThrowIfNull(guard);
        var safe = await guard.EnforceAsync(sql, context, ct).ConfigureAwait(false);
        return await connection.QueryAsync<T>(new CommandDefinition(safe, param, transaction, cancellationToken: ct)).ConfigureAwait(false);
    }

    /// <summary>Dapper: like <see cref="QueryGuardedAsync{T}"/> with dynamic rows.</summary>
    public static async Task<IEnumerable<dynamic>> QueryGuardedAsync(this IDbConnection connection, ISqlGuard guard, string sql, object? param = null,
        IReadOnlyDictionary<string, object?>? context = null, IDbTransaction? transaction = null, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(connection);
        ArgumentNullException.ThrowIfNull(guard);
        var safe = await guard.EnforceAsync(sql, context, ct).ConfigureAwait(false);
        return await connection.QueryAsync(new CommandDefinition(safe, param, transaction, cancellationToken: ct)).ConfigureAwait(false);
    }
}
