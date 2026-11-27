using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace SqlGuardLlm;

/// <summary>Entry points to the guard. Remote mode is <see cref="SqlGuardClient"/>; local mode is here.</summary>
public static class SqlGuard
{
    /// <summary>
    /// Starts the engine shipped in this package (no Python, no server) and returns a guard with the same API as
    /// <see cref="SqlGuardClient"/>. The packaged files are verified against <c>engine.sha256</c> first.
    /// Throws <see cref="SqlGuardException"/> (<c>engine_unsupported</c> names remote mode) if it cannot start.
    /// </summary>
    public static Task<SqlGuardLocal> CreateLocalAsync(SqlGuardLocalOptions options, CancellationToken ct = default)
        => SqlGuardLocal.CreateAsync(options, () => EngineLocator.Resolve(EngineLocator.CurrentRid(), AppContext.BaseDirectory), ct);
}

/// <summary>Settings of local mode.</summary>
public sealed class SqlGuardLocalOptions
{
    /// <summary>The policy as the JSON object of the policy file, e.g. <c>{"dialect": "postgres", "tables": ["invoices"]}</c>.</summary>
    public required string PolicyJson { get; set; }
    /// <summary>Language of the violation messages (<c>en</c> or <c>es</c>); the engine default when null.</summary>
    public string? Language { get; set; }
    /// <summary>Per-call timeout. A call that exceeds it fails and the engine is restarted. Default 5 seconds.</summary>
    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(5);
    /// <summary>Wait before the first restart after a crash; it doubles on every consecutive failure. Default 100 ms.</summary>
    public TimeSpan RestartBackoff { get; set; } = TimeSpan.FromMilliseconds(100);
    /// <summary>Upper bound of the restart wait. Default 5 seconds.</summary>
    public TimeSpan MaxRestartBackoff { get; set; } = TimeSpan.FromSeconds(5);
}

/// <summary>
/// The engine of this package as a child process (<c>sqlguardllm-engine --stdio</c>, protocol 1, see docs/PROTOCOL.md),
/// behind the same API as <see cref="SqlGuardClient"/>. Thread-safe: concurrent calls share one process and are matched to
/// their answers by request id. FAIL CLOSED: a binary that does not match its SHA-256, a wrong version or protocol,
/// a dead process, a timeout or any unexpected answer throws <see cref="SqlGuardException"/>; nothing is ever reported ok.
/// If the engine dies, every pending call fails; calls during the restart backoff fail at once with
/// <c>engine_restarting</c>, and the first call after it starts a fresh (re-verified) engine.
/// </summary>
public sealed class SqlGuardLocal : ISqlGuard
{
    private readonly Func<EngineLaunch> _resolve;
    private readonly JsonElement _policy;
    private readonly string? _language;
    private readonly TimeSpan _timeout, _initialBackoff, _maxBackoff;
    private readonly SemaphoreSlim _startGate = new(1, 1);
    private readonly object _backoffLock = new();
    private EngineSession? _session;
    private int _failures;
    private long _nextStart; // Stopwatch timestamp
    private int _disposed;

    private SqlGuardLocal(SqlGuardLocalOptions o, JsonElement policy, Func<EngineLaunch> resolve)
    {
        _resolve = resolve; _policy = policy; _language = o.Language;
        _timeout = o.Timeout; _initialBackoff = o.RestartBackoff; _maxBackoff = o.MaxRestartBackoff;
    }

    internal static async Task<SqlGuardLocal> CreateAsync(SqlGuardLocalOptions options, Func<EngineLaunch> resolve, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (string.IsNullOrWhiteSpace(options.PolicyJson)) throw new ArgumentException("PolicyJson is required.", nameof(options));
        if (options.Timeout <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(options), "Timeout must be positive.");
        if (options.RestartBackoff < TimeSpan.Zero || options.MaxRestartBackoff < options.RestartBackoff)
            throw new ArgumentOutOfRangeException(nameof(options), "Restart backoff must be non-negative and not above its maximum.");
        JsonElement policy;
        try
        {
            using var doc = JsonDocument.Parse(options.PolicyJson);
            if (doc.RootElement.ValueKind != JsonValueKind.Object) throw new ArgumentException("PolicyJson must be a JSON object.", nameof(options));
            policy = doc.RootElement.Clone();
        }
        catch (JsonException e) { throw new ArgumentException("PolicyJson is not valid JSON.", nameof(options), e); }

        var guard = new SqlGuardLocal(options, policy, resolve);
        guard._session = await guard.StartSessionAsync(ct).ConfigureAwait(false);
        return guard;
    }

    /// <summary>Checks and rewrites a query. <paramref name="context"/> must come from your app, never from the model.</summary>
    public async Task<CheckResult> CheckAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
        => Wire.ParseCheck(await CallAsync("check", sql, context, ct).ConfigureAwait(false), "Engine");

    /// <summary>Returns the safe SQL or throws <see cref="GuardViolationException"/>.</summary>
    public async Task<string> EnforceAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var r = await CheckAsync(sql, context, ct).ConfigureAwait(false);
        return r.Ok ? r.Sql! : throw new GuardViolationException(r.Violations);
    }

    public async Task<ExplainResult> ExplainAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
        => Wire.ParseExplain(await CallAsync("explain", sql, context, ct).ConfigureAwait(false));

    private async Task<JsonElement> CallAsync(string type, string sql, IReadOnlyDictionary<string, object?>? context, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(sql);
        ct.ThrowIfCancellationRequested();
        var session = await GetSessionAsync(ct).ConfigureAwait(false);
        var res = await session.RequestAsync(type, w =>
        {
            w.WriteString("handle", session.Handle);
            w.WriteString("sql", sql);
            w.WritePropertyName("context");
            Wire.WriteValue(w, context, 0);
        }, _timeout, ct).ConfigureAwait(false);
        if (!res.TryGetProperty("result", out var body)) throw new SqlGuardException("bad_response", $"Engine answered {type} without a result.");
        if (Volatile.Read(ref _failures) != 0) lock (_backoffLock) _failures = 0; // it served a request: a crash loop is over
        return body;
    }

    private async ValueTask<EngineSession> GetSessionAsync(CancellationToken ct)
    {
        ThrowIfDisposed();
        var s = Volatile.Read(ref _session);
        if (s is { IsDead: false }) return s;
        await _startGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            ThrowIfDisposed();
            s = Volatile.Read(ref _session);
            if (s is { IsDead: false }) return s;
            long wait;
            lock (_backoffLock) wait = _nextStart - Stopwatch.GetTimestamp();
            if (wait > 0)
                throw new SqlGuardException("engine_restarting",
                    $"The sqlguardllm engine stopped and is restarting; try again in {Stopwatch.GetElapsedTime(0, wait).TotalMilliseconds:F0} ms.");
            s = await StartSessionAsync(ct).ConfigureAwait(false);
            Volatile.Write(ref _session, s);
            if (Volatile.Read(ref _disposed) != 0) { s.Fault("engine_closed", "The guard was disposed."); throw new ObjectDisposedException(nameof(SqlGuardLocal)); }
            return s;
        }
        finally { _startGate.Release(); }
    }

    /// <summary>Resolves (and so re-verifies) the engine and starts it. A failure sets the restart backoff.</summary>
    private async Task<EngineSession> StartSessionAsync(CancellationToken ct)
    {
        try
        {
            var launch = _resolve();
            return await EngineSession.StartAsync(launch, _policy, _language, _timeout, OnSessionDead, ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { throw; }
        catch (SqlGuardException) { RegisterFailure(); throw; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        { RegisterFailure(); throw new SqlGuardException("engine_start", "Cannot read the sqlguardllm engine files.", null, e); }
    }

    private void OnSessionDead(EngineSession s)
    {
        if (Volatile.Read(ref _disposed) != 0) return;
        if (Interlocked.CompareExchange(ref _session, null, s) == s) RegisterFailure();
    }

    private void RegisterFailure()
    {
        lock (_backoffLock)
        {
            _failures = Math.Min(_failures + 1, 30);
            var delay = TimeSpan.FromTicks((long)Math.Min(_maxBackoff.Ticks, _initialBackoff.Ticks * Math.Pow(2, _failures - 1)));
            _nextStart = Stopwatch.GetTimestamp() + (long)(delay.TotalSeconds * Stopwatch.Frequency);
        }
    }

    private void ThrowIfDisposed() => ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);

    /// <summary>Stops the engine. Pending calls fail with <c>engine_closed</c>.</summary>
    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        var s = Interlocked.Exchange(ref _session, null);
        if (s is null) return;
        s.Fault("engine_closed", "The guard was disposed.");
        try { await s.Reader.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); }
        catch (TimeoutException) { }
    }
}

/// <summary>One engine process: a reader loop that completes pending requests by id, and the writes to its stdin.</summary>
internal sealed class EngineSession
{
    private const int Protocol = 1;
    private const int MaxMessageBytes = 1024 * 1024;

    private sealed record Pending(string Type, TaskCompletionSource<JsonElement> Tcs);

    private readonly Process _proc;
    private readonly Stream _stdin;
    private readonly SemaphoreSlim _write = new(1, 1);
    private readonly ConcurrentDictionary<long, Pending> _pending = new();
    private readonly Action<EngineSession> _onDead;
    private long _id;
    private int _dead;

    public string Handle { get; private set; } = "";
    public Task Reader { get; private set; } = Task.CompletedTask;
    public bool IsDead => Volatile.Read(ref _dead) != 0;

    private EngineSession(Process proc, Action<EngineSession> onDead) { _proc = proc; _stdin = proc.StandardInput.BaseStream; _onDead = onDead; }

    public static async Task<EngineSession> StartAsync(
        EngineLaunch launch, JsonElement policy, string? language, TimeSpan timeout, Action<EngineSession> onDead, CancellationToken ct)
    {
        var psi = new ProcessStartInfo(launch.FileName)
        {
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true,
            StandardOutputEncoding = new UTF8Encoding(false), StandardErrorEncoding = new UTF8Encoding(false),
        };
        foreach (var a in launch.Arguments) psi.ArgumentList.Add(a);
        Process proc;
        try { proc = Process.Start(psi) ?? throw new SqlGuardException("engine_start", "The sqlguardllm engine did not start."); }
        catch (Exception e) when (e is Win32Exception or InvalidOperationException)
        { throw new SqlGuardException("engine_start", "The sqlguardllm engine did not start.", null, e); }

        _ = DrainAsync(proc.StandardError); // keep the pipe empty; diagnostics are never surfaced
        var session = new EngineSession(proc, onDead);
        session.Reader = Task.Run(session.ReadLoopAsync, CancellationToken.None);
        try
        {
            var hello = await session.RequestAsync("hello", static _ => { }, timeout, ct).ConfigureAwait(false);
            CheckHello(hello);
            var loaded = await session.RequestAsync("load_policy", w =>
            {
                w.WritePropertyName("policy");
                policy.WriteTo(w);
                if (language is not null) w.WriteString("language", language);
            }, timeout, ct).ConfigureAwait(false);
            session.Handle = loaded.TryGetProperty("handle", out var h) && h.ValueKind == JsonValueKind.String ? h.GetString()! : "";
            if (session.Handle.Length == 0) throw new SqlGuardException("bad_response", "Engine answered load_policy without a handle.");
            return session;
        }
        catch { session.Fault("engine_closed", "The sqlguardllm engine failed to start."); throw; }
    }

    private static async Task DrainAsync(StreamReader stderr)
    {
        try { await stderr.ReadToEndAsync().ConfigureAwait(false); }
        catch (Exception e) when (e is IOException or ObjectDisposedException or InvalidOperationException) { }
    }

    private static void CheckHello(JsonElement hello)
    {
        var protocol = hello.TryGetProperty("protocol", out var p) && p.ValueKind == JsonValueKind.Number && p.TryGetInt32(out var n) ? n : -1;
        var version = hello.TryGetProperty("engine_version", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        var own = typeof(SqlGuardLocal).Assembly.GetName().Version;
        if (protocol != Protocol || !EngineLocator.LockstepOk(version, own?.Major ?? 0, own?.Minor ?? 0))
            throw new SqlGuardException("engine_version",
                $"Refusing the sqlguardllm engine: it reports version {version ?? "?"} protocol {protocol}; this package needs the same major.minor and protocol {Protocol}.");
    }

    /// <summary>Sends one request and waits for the answer with the same id. Any number of calls may run at once.</summary>
    public async Task<JsonElement> RequestAsync(string type, Action<Utf8JsonWriter> fields, TimeSpan timeout, CancellationToken ct)
    {
        var id = Interlocked.Increment(ref _id);
        var line = Wire.EngineRequest(id, type, fields);
        if (line.Length > MaxMessageBytes) throw new SqlGuardException("message_too_large", "The query and context are larger than the 1 MiB the engine accepts.");
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = new Pending(type, tcs);
        if (IsDead) { _pending.TryRemove(id, out _); throw Closed(); }
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout);
        try
        {
            await WriteAsync(line, cts.Token).ConfigureAwait(false);
            return await tcs.Task.WaitAsync(cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            _pending.TryRemove(id, out _); // a late answer is ignored: ids keep the pipe in sync
            throw;
        }
        catch (OperationCanceledException e)
        {
            _pending.TryRemove(id, out _);
            Fault("engine_closed", "The sqlguardllm engine was stopped because a request timed out."); // it may be hung
            throw new SqlGuardException("timeout", "Request to the sqlguardllm engine timed out.", null, e);
        }
    }

    private async Task WriteAsync(byte[] line, CancellationToken ct)
    {
        await _write.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (IsDead) throw Closed();
            try
            {
                await _stdin.WriteAsync(line, ct).ConfigureAwait(false);
                await _stdin.FlushAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            { Fault("engine_closed", "Writing to the sqlguardllm engine was interrupted."); throw; } // a half-written line would desync the pipe
            catch (Exception e) when (e is IOException or ObjectDisposedException or InvalidOperationException)
            { Fault("engine_closed", "Lost the sqlguardllm engine process.", e); throw Closed(e); }
        }
        finally { _write.Release(); }
    }

    private async Task ReadLoopAsync()
    {
        try
        {
            string? line;
            while ((line = await _proc.StandardOutput.ReadLineAsync().ConfigureAwait(false)) is not null)
                if (!Dispatch(line)) return;
            Fault("engine_closed", "The sqlguardllm engine exited.");
        }
        catch (Exception e) when (e is IOException or ObjectDisposedException or InvalidOperationException)
        { Fault("engine_closed", "Lost the sqlguardllm engine process.", e); }
        finally
        {
            Fault("engine_closed", "The sqlguardllm engine exited.");
            _proc.Dispose();
        }
    }

    /// <summary>Completes the request an answer belongs to. Returns false (and fails everything) on anything unexpected.</summary>
    private bool Dispatch(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("id", out var idEl)
                || idEl.ValueKind != JsonValueKind.Number || !idEl.TryGetInt64(out var id))
            {
                // An error without our id (e.g. message_too_large) means we cannot tell which request failed.
                Fault("bad_response", "Engine answered something unexpected.");
                return false;
            }
            if (!_pending.TryRemove(id, out var pending)) return true; // the call timed out or was cancelled
            var type = root.TryGetProperty("type", out var t) && t.ValueKind == JsonValueKind.String ? t.GetString() : null;
            if (type == "error")
            {
                var code = root.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.Object
                    && e.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String ? c.GetString()! : "engine_error";
                pending.Tcs.TrySetException(new SqlGuardException(code, $"Engine refused the request ({code})."));
                return true;
            }
            if (type != pending.Type)
            {
                pending.Tcs.TrySetException(new SqlGuardException("bad_response", "Engine answered out of order."));
                Fault("bad_response", "Engine answered out of order.");
                return false;
            }
            pending.Tcs.TrySetResult(root.Clone());
            return true;
        }
        catch (JsonException e)
        {
            Fault("bad_response", "Engine answered something unexpected.", e);
            return false;
        }
    }

    /// <summary>Ends this session once: kills the process and fails every pending call. Safe from any thread.</summary>
    public void Fault(string code, string message, Exception? inner = null)
    {
        if (Interlocked.Exchange(ref _dead, 1) != 0) return;
        try { _proc.Kill(true); }
        catch (Exception e) when (e is InvalidOperationException or Win32Exception or NotSupportedException) { } // already gone
        foreach (var id in _pending.Keys)
            if (_pending.TryRemove(id, out var p)) p.Tcs.TrySetException(new SqlGuardException(code, message, null, inner));
        _onDead(this);
    }

    private static SqlGuardException Closed(Exception? inner = null)
        => new("engine_closed", "The sqlguardllm engine is no longer running.", null, inner);
}
