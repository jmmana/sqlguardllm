using System.Text.Json;
using Microsoft.SemanticKernel;

namespace SqlGuardLlm.SemanticKernel;

/// <summary>Settings of <see cref="SqlGuardFunctionFilter"/>.</summary>
public sealed class SqlGuardFilterOptions
{
    /// <summary>Name of the function argument that carries the SQL. Default <c>sql</c>.</summary>
    public string ArgumentName { get; set; } = "sql";
    /// <summary>Which functions to check; by default every function that has the <see cref="ArgumentName"/> argument. Receives plugin name (may be null) and function name.</summary>
    public Func<string?, string, bool>? AppliesTo { get; set; }
    /// <summary>Where the context (user, tenant) comes from. It must not come from the model.</summary>
    public ISqlGuardContextProvider? Context { get; set; }
}

/// <summary>
/// Semantic Kernel function invocation filter: the SQL argument is checked by sqlguardllm before the function runs, and the
/// function receives the rewritten SQL. A rejected query does not run the function; its result is a JSON text
/// <c>{"ok":false,"violations":[...]}</c> so the model can correct itself. If the guard fails, or the argument is not a string,
/// the exception propagates and the function does not run: fail closed.
/// Register with <c>kernel.FunctionInvocationFilters.Add(new SqlGuardFunctionFilter(guard))</c>.
/// </summary>
public sealed class SqlGuardFunctionFilter : IFunctionInvocationFilter
{
    private readonly ISqlGuard _guard;
    private readonly SqlGuardFilterOptions _options;

    public SqlGuardFunctionFilter(ISqlGuard guard, SqlGuardFilterOptions? options = null)
    {
        _guard = guard ?? throw new ArgumentNullException(nameof(guard));
        _options = options ?? new SqlGuardFilterOptions();
    }

    public async Task OnFunctionInvocationAsync(FunctionInvocationContext context, Func<FunctionInvocationContext, Task> next)
    {
        var name = _options.ArgumentName;
        var applies = _options.AppliesTo is { } f
            ? f(context.Function.PluginName, context.Function.Name)
            : context.Arguments.ContainsName(name);
        if (!applies) { await next(context).ConfigureAwait(false); return; }

        var sql = context.Arguments.TryGetValue(name, out var raw) ? raw switch
        {
            string s => s,
            JsonElement { ValueKind: JsonValueKind.String } e => e.GetString(),
            _ => null,
        } : null;
        if (sql is null) throw new SqlGuardException("bad_argument", $"The '{name}' argument is missing or not a string; the function was not run.");

        var r = await _guard.CheckAsync(sql, _options.Context?.GetContext(), context.CancellationToken).ConfigureAwait(false);
        if (!r.Ok)
        {
            context.Result = new FunctionResult(context.Function, RejectionJson(r));
            return;
        }
        context.Arguments[name] = r.Sql;
        await next(context).ConfigureAwait(false);
    }

    private static string RejectionJson(CheckResult r)
    {
        using var ms = new MemoryStream();
        using (var w = new Utf8JsonWriter(ms))
        {
            w.WriteStartObject();
            w.WriteBoolean("ok", false);
            w.WriteStartArray("violations");
            foreach (var v in r.Violations)
            {
                w.WriteStartObject();
                w.WriteString("code", v.Code);
                w.WriteString("message", v.Message);
                w.WriteEndObject();
            }
            w.WriteEndArray();
            w.WriteEndObject();
        }
        return System.Text.Encoding.UTF8.GetString(ms.ToArray());
    }
}
