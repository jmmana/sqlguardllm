// Runs conformance/cases.json (the attack corpus) through local mode, i.e. over the stdio protocol of the engine.
// SQLGUARD_ENGINE_CMD is the engine command, e.g. the packaged binary "/path/sqlguardllm-engine --stdio"
// (default: uv run --project <repo> sqlguardllm engine --stdio). Passes with a warning when it does not run,
// unless SQLGUARD_REQUIRE_CONFORMANCE=1.
using System.Diagnostics;
using SqlGuardLlm;
using Xunit;

public class LocalConformanceTests
{
    private static readonly string[] Launcher =
        (Environment.GetEnvironmentVariable("SQLGUARD_ENGINE_CMD") ?? $"uv run --project {ConformanceCorpus.Root} sqlguardllm engine --stdio").Split(' ');

    public static IEnumerable<object[]> GroupIndexes() => ConformanceCorpus.GroupIndexes();

    private static bool Available()
    {
        try
        {
            var psi = new ProcessStartInfo(Launcher[0]) { RedirectStandardInput = true, RedirectStandardError = true, RedirectStandardOutput = true, WorkingDirectory = ConformanceCorpus.Root };
            foreach (var a in Launcher.Skip(1)) psi.ArgumentList.Add(a);
            using var p = Process.Start(psi)!;
            p.StandardInput.Close(); // the engine exits on stdin EOF
            if (p.WaitForExit(60_000) && p.ExitCode == 0) return true;
            if (!p.HasExited) p.Kill(true);
        }
        catch (System.ComponentModel.Win32Exception) { }
        if (ConformanceCorpus.Required) throw new InvalidOperationException($"SQLGUARD_REQUIRE_CONFORMANCE=1 but \"{string.Join(' ', Launcher)}\" does not run");
        Console.Error.WriteLine($"local conformance skipped: \"{string.Join(' ', Launcher)}\" is not available");
        return false;
    }

    [Theory]
    [MemberData(nameof(GroupIndexes))]
    public async Task Group_matches_in_process_guard(int index)
    {
        if (!Available()) return;
        var group = ConformanceCorpus.Groups[index];
        var options = new SqlGuardLocalOptions { PolicyJson = group.GetProperty("policy").GetRawText(), Timeout = TimeSpan.FromSeconds(60) };
        await using var guard = await SqlGuardLocal.CreateAsync(options, () => new EngineLaunch(Launcher[0], Launcher.Skip(1).ToArray()), default);
        await ConformanceCorpus.AssertGroupAsync(guard, group);
    }

    [Fact]
    public async Task Whole_corpus_runs_concurrently_on_one_engine()
    {
        if (!Available()) return;
        var group = ConformanceCorpus.Groups[0];
        var options = new SqlGuardLocalOptions { PolicyJson = group.GetProperty("policy").GetRawText(), Timeout = TimeSpan.FromSeconds(60) };
        await using var guard = await SqlGuardLocal.CreateAsync(options, () => new EngineLaunch(Launcher[0], Launcher.Skip(1).ToArray()), default);
        await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => ConformanceCorpus.AssertGroupAsync(guard, group)));
    }
}
