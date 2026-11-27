using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace SqlGuardLlm;

/// <summary>One reason a query was rejected.</summary>
public sealed record Violation(
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("detail")] string Detail = "");

/// <summary>Result of a check. <see cref="Ok"/> true means <see cref="Sql"/> is safe to run.</summary>
public sealed record CheckResult
{
    [JsonPropertyName("ok")] public bool Ok { get; init; }
    [JsonPropertyName("sql")] public string? Sql { get; init; }
    [JsonPropertyName("tables")] public IReadOnlyList<string> Tables { get; init; } = Array.Empty<string>();
    [JsonPropertyName("applied_filters")] public IReadOnlyList<string> AppliedFilters { get; init; } = Array.Empty<string>();
    [JsonPropertyName("violations")] public IReadOnlyList<Violation> Violations { get; init; } = Array.Empty<Violation>();
}

/// <summary>Result of /v1/explain. <see cref="Raw"/> holds the whole JSON object.</summary>
public sealed record ExplainResult(
    bool Ok, string? Sql, IReadOnlyList<string> Tables, IReadOnlyList<Violation> Violations, JsonElement Raw);

/// <summary>Result of /v1/health.</summary>
public sealed record HealthResult(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("version")] string Version);

/// <summary>
/// The guard API, identical in remote mode (<see cref="SqlGuardClient"/>) and local mode
/// (<see cref="SqlGuardLocal"/>). FAIL CLOSED: any failure throws <see cref="SqlGuardException"/>;
/// a rejected query is not an error (<c>Ok = false</c>). Do not catch the exception and run the SQL anyway.
/// </summary>
public interface ISqlGuard : IAsyncDisposable
{
    /// <summary>Checks and rewrites a query. <paramref name="context"/> must come from your app (user, tenant), never from the model.</summary>
    Task<CheckResult> CheckAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default);

    /// <summary>Returns the safe SQL or throws <see cref="GuardViolationException"/>.</summary>
    Task<string> EnforceAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default);

    /// <summary>Like check, with the full explanation of the decision.</summary>
    Task<ExplainResult> ExplainAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default);
}

/// <summary>Client settings.</summary>
public sealed class SqlGuardOptions
{
    /// <summary>Base URL of <c>sqlguardllm serve</c>, e.g. http://localhost:8080.</summary>
    public Uri BaseUrl { get; set; } = new("http://localhost:8080");
    /// <summary>Optional bearer token. Never logged or put in exceptions.</summary>
    public string? Token { get; set; }
    /// <summary>Per-request timeout. Default 5 seconds.</summary>
    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(5);
}

/// <summary>Any failure talking to the server (network, timeout, HTTP error, bad response).
/// Never contains the token or the context.</summary>
public class SqlGuardException : Exception
{
    public string Code { get; }
    public int? StatusCode { get; }
    public SqlGuardException(string code, string message, int? statusCode = null, Exception? inner = null)
        : base(message, inner) { Code = code; StatusCode = statusCode; }
}

/// <summary>Thrown by <c>EnforceAsync</c> when the query is rejected.</summary>
public sealed class GuardViolationException : SqlGuardException
{
    public IReadOnlyList<Violation> Violations { get; }
    public GuardViolationException(IReadOnlyList<Violation> violations)
        : base("query_rejected",
               "Query rejected by sqlguardllm: " + (violations.Count == 0 ? "no details" : string.Join(", ", violations.Select(v => v.Code))))
    { Violations = violations; }
}

/// <summary>
/// Client for <c>sqlguardllm serve</c>. FAIL CLOSED: if the server is unreachable, times out
/// or answers something unexpected, methods throw <see cref="SqlGuardException"/>; they never
/// return an ok result. Do not catch it and run the SQL anyway.
/// </summary>
public sealed class SqlGuardClient : ISqlGuard, IDisposable
{
    private readonly HttpClient _http;
    private readonly SqlGuardOptions _options;
    private readonly bool _owns;

    /// <summary>Use your own (ideally factory-managed) HttpClient.</summary>
    public SqlGuardClient(HttpClient http, SqlGuardOptions options)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _options = options ?? throw new ArgumentNullException(nameof(options));
    }

    /// <summary>Convenience: creates and owns an HttpClient.</summary>
    public SqlGuardClient(string baseUrl, string? token = null, TimeSpan? timeout = null)
        : this(new HttpClient(), new SqlGuardOptions { BaseUrl = new Uri(baseUrl), Token = token, Timeout = timeout ?? TimeSpan.FromSeconds(5) })
    { _owns = true; }

    public void Dispose() { if (_owns) _http.Dispose(); }

    public ValueTask DisposeAsync() { Dispose(); return ValueTask.CompletedTask; }

    /// <summary>Checks and rewrites a query. <paramref name="context"/> must come from your app (user, tenant), never from the model.</summary>
    public async Task<CheckResult> CheckAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var raw = await SendAsync(HttpMethod.Post, "/v1/check", Wire.HttpBody(sql, context), SqlGuardJsonContext.Default.JsonElement, ct).ConfigureAwait(false);
        return Wire.ParseCheck(raw, "Server");
    }

    /// <summary>Returns the safe SQL or throws <see cref="GuardViolationException"/>.</summary>
    public async Task<string> EnforceAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var r = await CheckAsync(sql, context, ct).ConfigureAwait(false);
        return r.Ok ? r.Sql! : throw new GuardViolationException(r.Violations);
    }

    public async Task<ExplainResult> ExplainAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
        => Wire.ParseExplain(await SendAsync(HttpMethod.Post, "/v1/explain", Wire.HttpBody(sql, context), SqlGuardJsonContext.Default.JsonElement, ct).ConfigureAwait(false));

    public Task<HealthResult> HealthAsync(CancellationToken ct = default)
        => SendAsync(HttpMethod.Get, "/v1/health", null, SqlGuardJsonContext.Default.HealthResult, ct);

    private async Task<T> SendAsync<T>(HttpMethod method, string path, byte[]? body, JsonTypeInfo<T> type, CancellationToken ct)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(_options.Timeout);
        using var req = new HttpRequestMessage(method, new Uri(_options.BaseUrl, path.TrimStart('/')));
        if (!string.IsNullOrEmpty(_options.Token))
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _options.Token);
        if (body is not null)
        {
            req.Content = new ByteArrayContent(body); // a known length: the server rejects chunked bodies
            req.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
        }
        try
        {
            using var resp = await _http.SendAsync(req, cts.Token).ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode) throw HttpError((int)resp.StatusCode, text);
            var result = JsonSerializer.Deserialize(text, type);
            if (result is null) throw new SqlGuardException("bad_response", "Empty response from server.", (int)resp.StatusCode);
            return result;
        }
        catch (SqlGuardException) { throw; }
        catch (OperationCanceledException e) when (!ct.IsCancellationRequested)
        { throw new SqlGuardException("timeout", "Request to sqlguardllm timed out.", null, e); }
        catch (OperationCanceledException) { throw; }
        catch (HttpRequestException e)
        { throw new SqlGuardException("unreachable", "Could not reach the sqlguardllm server.", null, e); }
        catch (JsonException e)
        { throw new SqlGuardException("bad_response", "Server answered something unexpected.", null, e); }
    }

    private static SqlGuardException HttpError(int status, string text)
    {
        string code = "http_" + status, msg = $"Server returned HTTP {status}.";
        try
        {
            using var d = JsonDocument.Parse(text);
            if (d.RootElement.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.Object)
            {
                if (e.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String) code = c.GetString()!;
                if (e.TryGetProperty("message", out var m) && m.ValueKind == JsonValueKind.String) msg = $"HTTP {status}: {m.GetString()}";
            }
        }
        catch (JsonException) { }
        return new SqlGuardException(code, msg, status);
    }
}
