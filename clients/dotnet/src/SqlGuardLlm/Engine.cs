using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

[assembly: System.Runtime.CompilerServices.InternalsVisibleTo("SqlGuardLlm.Tests")]

namespace SqlGuardLlm;

/// <summary>
/// Finds the engine shipped in this package (<c>runtimes/{rid}/native/sqlguardllm-engine/</c>)
/// and checks it against <c>engine.sha256</c> before anything is started.
/// </summary>
internal static class EngineLocator
{
    public const string FolderName = "sqlguardllm-engine";
    public const string ManifestName = "engine.sha256";
    private static readonly string[] SupportedRids = ["linux-x64", "linux-arm64", "osx-x64", "osx-arm64", "win-x64"];
    private const string RemoteHint = "Use remote mode instead: run `sqlguardllm serve` and connect with SqlGuardClient.";

    /// <summary>The package RID of this process, or a failure that names remote mode.</summary>
    public static string CurrentRid()
    {
        var runtimeRid = RuntimeInformation.RuntimeIdentifier;
        if (runtimeRid.Contains("musl", StringComparison.OrdinalIgnoreCase))
            throw Unsupported($"the engine is built for glibc Linux, not musl ({runtimeRid})");
        var os = OperatingSystem.IsLinux() ? "linux" : OperatingSystem.IsMacOS() ? "osx" : OperatingSystem.IsWindows() ? "win" : null;
        var arch = RuntimeInformation.ProcessArchitecture switch { Architecture.X64 => "x64", Architecture.Arm64 => "arm64", _ => null };
        if (os is null || arch is null || Array.IndexOf(SupportedRids, $"{os}-{arch}") < 0)
            throw Unsupported($"no engine for {RuntimeInformation.OSDescription} on {RuntimeInformation.ProcessArchitecture}");
        return $"{os}-{arch}";
    }

    public static SqlGuardException Unsupported(string why)
        => new("engine_unsupported", $"The embedded sqlguardllm engine does not run here: {why}. {RemoteHint}");

    /// <summary>Folder holding the engine for <paramref name="rid"/>, searching the app folder.</summary>
    public static string Locate(string baseDirectory, string rid)
    {
        // `dotnet run`/build keep runtimes/{rid}/native/; a publish for one RID puts the folder next to the app.
        foreach (var dir in new[] { Path.Combine(baseDirectory, "runtimes", rid, "native", FolderName), Path.Combine(baseDirectory, FolderName) })
            if (File.Exists(Path.Combine(dir, ManifestName))) return dir;
        throw Unsupported($"this package carries no engine for {rid} (looked in {Path.Combine(baseDirectory, "runtimes", rid, "native")})");
    }

    /// <summary>
    /// Recomputes the SHA-256 of every file listed in the manifest (lines <c>&lt;hex&gt;  &lt;relative path&gt;</c>).
    /// Throws on a missing file, a mismatch or a manifest without the executable. Returns the executable path.
    /// </summary>
    public static string Verify(string folder, string rid)
    {
        var exe = rid.StartsWith("win-", StringComparison.Ordinal) ? "sqlguardllm-engine.exe" : "sqlguardllm-engine";
        var listed = false;
        var root = Path.GetFullPath(folder) + Path.DirectorySeparatorChar;
        foreach (var line in File.ReadLines(Path.Combine(folder, ManifestName)))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            var sep = line.IndexOf("  ", StringComparison.Ordinal);
            if (sep != 64) throw Refuse("malformed manifest line");
            var expected = line[..64];
            var rel = line[(sep + 2)..];
            var full = Path.GetFullPath(Path.Combine(folder, rel));
            if (!full.StartsWith(root, StringComparison.Ordinal)) throw Refuse($"path outside the engine folder: {rel}");
            if (!File.Exists(full)) throw Refuse($"missing file {rel}");
            string actual;
            using (var s = File.OpenRead(full)) actual = Convert.ToHexStringLower(SHA256.HashData(s));
            if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase)) throw Refuse($"SHA-256 mismatch for {rel}");
            if (rel == exe) listed = true;
        }
        if (!listed) throw Refuse($"the manifest does not list {exe}");
        return Path.Combine(folder, exe);
    }

    private static SqlGuardException Refuse(string why)
        => new("engine_integrity", $"Refusing to start the sqlguardllm engine: {why}. Reinstall the package.");
}

/// <summary>
/// The engine of this package as a child process (<c>sqlguardllm-engine --stdio</c>, protocol 1,
/// see docs/PROTOCOL.md). No Python and no server needed. FAIL CLOSED like <see cref="SqlGuardClient"/>:
/// a binary that does not match its SHA-256, a wrong version or protocol, a dead process, a timeout or
/// any unexpected answer throws <see cref="SqlGuardException"/>; nothing is ever reported ok.
/// </summary>
public sealed class SqlGuardEngine : IDisposable
{
    private const int Protocol = 1;
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    private readonly Process _proc;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly TimeSpan _timeout;
    private string _handle = "";
    private int _id;
    private bool _broken;

    private SqlGuardEngine(Process proc, TimeSpan timeout) { _proc = proc; _timeout = timeout; }

    /// <summary>Verifies the packaged engine, starts it, checks version and protocol and loads the policy
    /// (the object form of the policy file, e.g. <c>{"dialect": "postgres", "tables": ["invoices"]}</c>).</summary>
    public static async Task<SqlGuardEngine> StartAsync(object policy, TimeSpan? timeout = null, CancellationToken ct = default)
        => await StartAsync(policy, EngineLocator.CurrentRid(), AppContext.BaseDirectory, timeout, ct).ConfigureAwait(false);

    internal static async Task<SqlGuardEngine> StartAsync(object policy, string rid, string baseDirectory, TimeSpan? timeout, CancellationToken ct)
    {
        var folder = EngineLocator.Locate(baseDirectory, rid);
        var exe = EngineLocator.Verify(folder, rid);
        if (!OperatingSystem.IsWindows())
        {
            try { File.SetUnixFileMode(exe, File.GetUnixFileMode(exe) | UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            { throw new SqlGuardException("engine_start", "Cannot make the sqlguardllm engine executable (read-only file system?).", null, e); }
        }
        var psi = new ProcessStartInfo(exe, "--stdio")
        {
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true,
            StandardOutputEncoding = new UTF8Encoding(false), StandardInputEncoding = new UTF8Encoding(false),
        };
        Process proc;
        try { proc = Process.Start(psi) ?? throw new SqlGuardException("engine_start", "The sqlguardllm engine did not start."); }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException)
        { throw new SqlGuardException("engine_start", "The sqlguardllm engine did not start.", null, e); }
        _ = proc.StandardError.ReadToEndAsync(); // keep the pipe drained; diagnostics are never surfaced
        var engine = new SqlGuardEngine(proc, timeout ?? TimeSpan.FromSeconds(5));
        try
        {
            var hello = await engine.RequestAsync("hello", new Dictionary<string, object?>(), ct).ConfigureAwait(false);
            engine.CheckHello(hello);
            var loaded = await engine.RequestAsync("load_policy", new Dictionary<string, object?> { ["policy"] = policy }, ct).ConfigureAwait(false);
            engine._handle = loaded.TryGetProperty("handle", out var h) && h.ValueKind == JsonValueKind.String ? h.GetString()! : "";
            if (engine._handle.Length == 0) throw new SqlGuardException("bad_response", "Engine answered load_policy without a handle.");
            return engine;
        }
        catch { engine.Dispose(); throw; }
    }

    private void CheckHello(JsonElement hello)
    {
        var protocol = hello.TryGetProperty("protocol", out var p) && p.ValueKind == JsonValueKind.Number && p.TryGetInt32(out var n) ? n : -1;
        var version = hello.TryGetProperty("engine_version", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        var own = typeof(SqlGuardEngine).Assembly.GetName().Version;
        if (protocol != Protocol || !LockstepOk(version, own?.Major ?? 0, own?.Minor ?? 0))
            throw new SqlGuardException("engine_version",
                $"Refusing the sqlguardllm engine: it reports version {version ?? "?"} protocol {protocol}; this package needs the same major.minor and protocol {Protocol}.");
    }

    /// <summary>Same major version and, before 1.0, the same minor version (docs/PROTOCOL.md).</summary>
    internal static bool LockstepOk(string? engineVersion, int major, int minor)
    {
        if (engineVersion is null) return false;
        var parts = engineVersion.Split('.');
        if (parts.Length < 2 || !int.TryParse(parts[0], out var emaj) || !int.TryParse(parts[1], out var emin)) return false;
        return emaj == major && (major >= 1 || emin == minor);
    }

    /// <summary>Checks and rewrites a query. <paramref name="context"/> must come from your app, never from the model.</summary>
    public async Task<CheckResult> CheckAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var res = await RequestAsync("check", new Dictionary<string, object?> { ["handle"] = _handle, ["sql"] = sql, ["context"] = context }, ct).ConfigureAwait(false);
        try
        {
            var r = res.TryGetProperty("result", out var body) && body.ValueKind == JsonValueKind.Object ? body.Deserialize<CheckResult>(Json) : null;
            if (r is null) throw new SqlGuardException("bad_response", "Engine answered check without a result.");
            if (r.Ok && string.IsNullOrEmpty(r.Sql)) throw new SqlGuardException("bad_response", "Engine answered ok without SQL; treating as failure.");
            return r;
        }
        catch (JsonException e) { throw new SqlGuardException("bad_response", "Engine answered something unexpected.", null, e); }
    }

    /// <summary>Returns the safe SQL or throws <see cref="GuardViolationException"/>.</summary>
    public async Task<string> EnforceAsync(string sql, IReadOnlyDictionary<string, object?>? context = null, CancellationToken ct = default)
    {
        var r = await CheckAsync(sql, context, ct).ConfigureAwait(false);
        return r.Ok ? r.Sql! : throw new GuardViolationException(r.Violations);
    }

    private async Task<JsonElement> RequestAsync(string type, Dictionary<string, object?> fields, CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_broken) throw new SqlGuardException("engine_closed", "The sqlguardllm engine is no longer usable.");
            var id = ++_id;
            fields["id"] = id; fields["type"] = type;
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(_timeout);
            try
            {
                await _proc.StandardInput.WriteLineAsync(JsonSerializer.Serialize(fields)).ConfigureAwait(false);
                await _proc.StandardInput.FlushAsync(cts.Token).ConfigureAwait(false);
                var line = await _proc.StandardOutput.ReadLineAsync(cts.Token).ConfigureAwait(false)
                    ?? throw new SqlGuardException("engine_closed", "The sqlguardllm engine exited.");
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement.Clone();
                if (root.ValueKind != JsonValueKind.Object) throw new SqlGuardException("bad_response", "Engine answered something unexpected.");
                if (root.TryGetProperty("type", out var t) && t.ValueKind == JsonValueKind.String && t.GetString() == "error")
                {
                    var code = root.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.Object && e.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String ? c.GetString()! : "engine_error";
                    throw new SqlGuardException(code, $"Engine refused the request ({code}).");
                }
                if (!root.TryGetProperty("id", out var rid) || rid.ValueKind != JsonValueKind.Number || rid.GetInt32() != id
                    || !root.TryGetProperty("type", out var rt) || rt.GetString() != type)
                    throw new SqlGuardException("bad_response", "Engine answered out of order.");
                return root;
            }
            catch (OperationCanceledException e) when (!ct.IsCancellationRequested)
            { _broken = true; throw new SqlGuardException("timeout", "Request to the sqlguardllm engine timed out.", null, e); }
            catch (OperationCanceledException) { _broken = true; throw; } // a half-read answer would desync the pipe
            catch (Exception e) when (e is IOException or JsonException or ObjectDisposedException or InvalidOperationException)
            { _broken = true; throw new SqlGuardException("engine_closed", "Lost the sqlguardllm engine process.", null, e); }
            catch (SqlGuardException) { _broken = true; throw; }
        }
        finally { _gate.Release(); }
    }

    public void Dispose()
    {
        _broken = true;
        try { if (!_proc.HasExited) { _proc.StandardInput.Close(); if (!_proc.WaitForExit(1000)) _proc.Kill(true); } }
        catch (Exception e) when (e is InvalidOperationException or IOException) { }
        _proc.Dispose();
    }
}
