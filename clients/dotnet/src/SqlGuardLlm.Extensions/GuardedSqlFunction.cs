using System.Text.Json;
using Microsoft.Extensions.AI;

namespace SqlGuardLlm;

/// <summary>
/// A Microsoft.Extensions.AI tool the model calls with <c>{"sql": "..."}</c>. The SQL is checked by the guard first and
/// <paramref name="execute"/> only ever receives the rewritten, safe SQL. A rejected query is NOT run: the model gets
/// <c>{"ok": false, "violations": [...]}</c> so it can correct itself. If the guard fails (server down, timeout) the
/// <see cref="SqlGuardException"/> propagates: fail closed.
/// </summary>
public sealed class GuardedSqlFunction : AIFunction
{
    private static readonly JsonElement Schema = JsonDocument.Parse(
        """{"type":"object","properties":{"sql":{"type":"string","description":"The SQL query to run."}},"required":["sql"],"additionalProperties":false}""").RootElement.Clone();

    private readonly ISqlGuard _guard;
    private readonly Func<string, CancellationToken, Task<object?>> _execute;
    private readonly ISqlGuardContextProvider? _context;

    public GuardedSqlFunction(ISqlGuard guard, Func<string, CancellationToken, Task<object?>> execute,
        ISqlGuardContextProvider? context = null, string name = "run_sql", string description = "Runs a read-only SQL query and returns the rows.")
    {
        _guard = guard ?? throw new ArgumentNullException(nameof(guard));
        _execute = execute ?? throw new ArgumentNullException(nameof(execute));
        _context = context;
        Name = name;
        Description = description;
    }

    public override string Name { get; }
    public override string Description { get; }
    public override JsonElement JsonSchema => Schema;

    protected override async ValueTask<object?> InvokeCoreAsync(AIFunctionArguments arguments, CancellationToken cancellationToken)
    {
        if (!arguments.TryGetValue("sql", out var raw) || ReadSql(raw) is not { } sql)
            throw new ArgumentException("The 'sql' argument is required and must be a string.", nameof(arguments));
        var r = await _guard.CheckAsync(sql, _context?.GetContext(), cancellationToken).ConfigureAwait(false);
        if (!r.Ok)
            return new Dictionary<string, object?>
            {
                ["ok"] = false,
                ["violations"] = r.Violations.Select(v => new Dictionary<string, object?> { ["code"] = v.Code, ["message"] = v.Message }).ToList(),
            };
        return await _execute(r.Sql!, cancellationToken).ConfigureAwait(false);
    }

    private static string? ReadSql(object? v) => v switch
    {
        string s => s,
        JsonElement { ValueKind: JsonValueKind.String } e => e.GetString(),
        _ => null,
    };
}
