using System.Runtime.InteropServices;
using System.Security.Cryptography;

[assembly: System.Runtime.CompilerServices.InternalsVisibleTo("SqlGuardLlm.Tests")]

namespace SqlGuardLlm;

/// <summary>A verified engine executable and its arguments.</summary>
internal sealed record EngineLaunch(string FileName, IReadOnlyList<string> Arguments);

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
            using (var s = File.OpenRead(full)) actual = Convert.ToHexString(SHA256.HashData(s));
            if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase)) throw Refuse($"SHA-256 mismatch for {rel}");
            if (rel == exe) listed = true;
        }
        if (!listed) throw Refuse($"the manifest does not list {exe}");
        return Path.Combine(folder, exe);
    }

    /// <summary>Locates and verifies the packaged engine and makes it executable. Run on every (re)start.</summary>
    public static EngineLaunch Resolve(string rid, string baseDirectory)
    {
        var exe = Verify(Locate(baseDirectory, rid), rid);
        if (!OperatingSystem.IsWindows())
        {
            try { File.SetUnixFileMode(exe, File.GetUnixFileMode(exe) | UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            { throw new SqlGuardException("engine_start", "Cannot make the sqlguardllm engine executable (read-only file system?).", null, e); }
        }
        return new EngineLaunch(exe, ["--stdio"]);
    }

    /// <summary>Same major version and, before 1.0, the same minor version (docs/PROTOCOL.md).</summary>
    public static bool LockstepOk(string? engineVersion, int major, int minor)
    {
        if (engineVersion is null) return false;
        var parts = engineVersion.Split('.');
        if (parts.Length < 2 || !int.TryParse(parts[0], out var emaj) || !int.TryParse(parts[1], out var emin)) return false;
        return emaj == major && (major >= 1 || emin == minor);
    }

    private static SqlGuardException Refuse(string why)
        => new("engine_integrity", $"Refusing to start the sqlguardllm engine: {why}. Reinstall the package.");
}
