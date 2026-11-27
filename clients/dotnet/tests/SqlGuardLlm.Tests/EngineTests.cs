using System.Security.Cryptography;
using System.Text;
using SqlGuardLlm;
using Xunit;

public class EngineTests
{
    private const string Rid = "linux-x64";

    private static string Sha(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text)));

    /// <summary>An app folder with runtimes/linux-x64/native/sqlguardllm-engine/{exe,_internal/lib,engine.sha256}.</summary>
    internal static (string app, string exe) Fake(string script = "#!/bin/sh\nexit 0\n")
    {
        // Under the test output folder, not /tmp: /tmp is often mounted noexec.
        var app = Directory.CreateDirectory(Path.Combine(AppContext.BaseDirectory, "engine-tests", Guid.NewGuid().ToString("N"))).FullName;
        var dir = Path.Combine(app, "runtimes", Rid, "native", "sqlguardllm-engine");
        Directory.CreateDirectory(Path.Combine(dir, "_internal"));
        File.WriteAllText(Path.Combine(dir, "sqlguardllm-engine"), script);
        File.WriteAllText(Path.Combine(dir, "_internal", "lib.so"), "lib");
        File.WriteAllText(Path.Combine(dir, "engine.sha256"), $"{Sha(script)}  sqlguardllm-engine\n{Sha("lib")}  _internal/lib.so\n");
        return (app, Path.Combine(dir, "sqlguardllm-engine"));
    }

    internal static Task<SqlGuardLocal> StartLocal(string app, string rid, string policy = "{}", SqlGuardLocalOptions? options = null)
    {
        options ??= new SqlGuardLocalOptions { PolicyJson = policy };
        return SqlGuardLocal.CreateAsync(options, () => EngineLocator.Resolve(rid, app), default);
    }

    [Fact] public void Verify_accepts_matching_files()
    {
        var (app, exe) = Fake();
        var folder = EngineLocator.Locate(app, Rid);
        Assert.Equal(exe, EngineLocator.Verify(folder, Rid));
    }

    [Fact] public async Task Modified_binary_refuses_to_start_and_is_never_run()
    {
        var marker = Path.Combine(Path.GetTempPath(), "sg-ran-" + Guid.NewGuid());
        var (app, exe) = Fake();
        File.WriteAllText(exe, $"#!/bin/sh\ntouch {marker}\n");   // tampered after packing
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app, Rid));
        Assert.Equal("engine_integrity", e.Code);
        Assert.Contains("mismatch", e.Message);
        Assert.False(File.Exists(marker));
    }

    [Fact] public async Task Modified_support_file_refuses_to_start()
    {
        var (app, exe) = Fake();
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(exe)!, "_internal", "lib.so"), "evil");
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app, Rid));
        Assert.Equal("engine_integrity", e.Code);
    }

    [Fact] public async Task Missing_file_and_manifest_without_exe_refuse()
    {
        var (app, exe) = Fake();
        File.Delete(Path.Combine(Path.GetDirectoryName(exe)!, "_internal", "lib.so"));
        Assert.Equal("engine_integrity", (await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app, Rid))).Code);

        var (app2, exe2) = Fake();
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(exe2)!, "engine.sha256"), $"{Sha("lib")}  _internal/lib.so\n");
        Assert.Equal("engine_integrity", (await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app2, Rid))).Code);
    }

    [Fact] public void Manifest_cannot_point_outside_the_folder()
    {
        var (app, exe) = Fake();
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(exe)!, "engine.sha256"), $"{Sha("x")}  ../../../../etc/hostname\n");
        var e = Assert.Throws<SqlGuardException>(() => EngineLocator.Verify(EngineLocator.Locate(app, Rid), Rid));
        Assert.Equal("engine_integrity", e.Code);
    }

    [Fact] public async Task Runtime_without_engine_names_remote_mode()
    {
        var app = Directory.CreateTempSubdirectory("sgempty").FullName;
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app, "freebsd-x64"));
        Assert.Equal("engine_unsupported", e.Code);
        Assert.Contains("remote mode", e.Message);
        Assert.Contains("SqlGuardClient", e.Message);
        Assert.Contains("remote mode", EngineLocator.Unsupported("musl").Message);
    }

    [Theory]
    [InlineData("0.5.0a3", 0, 5, true)]
    [InlineData("0.5.9", 0, 5, true)]
    [InlineData("0.6.0", 0, 5, false)]
    [InlineData("1.2.0", 1, 0, true)]
    [InlineData("2.0.0", 1, 0, false)]
    [InlineData("garbage", 0, 5, false)]
    [InlineData(null, 0, 5, false)]
    public void Lockstep(string? v, int major, int minor, bool ok) => Assert.Equal(ok, EngineLocator.LockstepOk(v, major, minor));

    [Fact] public async Task Talks_to_a_verified_engine_over_stdio()
    {
        if (OperatingSystem.IsWindows()) return;
        var script = """
            #!/bin/sh
            while IFS= read -r line; do
              case "$line" in
                *'"hello"'*) echo '{"id":1,"type":"hello","protocol":1,"engine_version":"0.5.0a3","dialects":[]}' ;;
                *'"load_policy"'*) echo '{"id":2,"type":"load_policy","handle":"p1"}' ;;
                *'"check"'*) echo '{"id":3,"type":"check","result":{"ok":true,"sql":"SELECT 1 LIMIT 10","tables":[],"applied_filters":[],"violations":[]}}' ;;
              esac
            done
            """.Replace("\r", "");
        var (app, _) = Fake(script + "\n");
        await using var engine = await StartLocal(app, Rid, """{"dialect":"postgres"}""");
        Assert.Equal("SELECT 1 LIMIT 10", await engine.EnforceAsync("SELECT 1"));
    }

    [Fact] public async Task Wrong_protocol_is_refused()
    {
        if (OperatingSystem.IsWindows()) return;
        var (app, _) = Fake("#!/bin/sh\nread l\necho '{\"id\":1,\"type\":\"hello\",\"protocol\":2,\"engine_version\":\"0.5.0\"}'\ncat >/dev/null\n");
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => StartLocal(app, Rid));
        Assert.Equal("engine_version", e.Code);
    }
}
