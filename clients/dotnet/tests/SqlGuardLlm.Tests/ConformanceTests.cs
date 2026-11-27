// Runs conformance/cases.json (the attack corpus) against a real `sqlguardllm serve`.
// Passes with a warning when no server executable exists, unless SQLGUARD_REQUIRE_CONFORMANCE=1.
// SQLGUARD_SERVER_CMD overrides the launcher (default: uv run --project <repo> --extra serve sqlguardllm).
using System.Diagnostics;
using System.Text.RegularExpressions;
using SqlGuardLlm;
using Xunit;

public class ConformanceTests
{
    private static readonly string Root = ConformanceCorpus.Root;
    private static readonly string[] Launcher =
        (Environment.GetEnvironmentVariable("SQLGUARD_SERVER_CMD") ?? $"uv run --project {Root} --extra serve sqlguardllm").Split(' ');

    public static IEnumerable<object[]> GroupIndexes() => ConformanceCorpus.GroupIndexes();

    private static Process Launch(IEnumerable<string> extra)
    {
        var psi = new ProcessStartInfo(Launcher[0]) { RedirectStandardError = true, RedirectStandardOutput = true, WorkingDirectory = Root };
        foreach (var a in Launcher.Skip(1).Concat(extra)) psi.ArgumentList.Add(a);
        return Process.Start(psi)!;
    }

    private static bool Available()
    {
        try
        {
            using var p = Launch(["--version"]);
            p.WaitForExit(60_000);
            if (p.ExitCode == 0) return true;
        }
        catch (System.ComponentModel.Win32Exception) { }
        if (ConformanceCorpus.Required) throw new InvalidOperationException($"SQLGUARD_REQUIRE_CONFORMANCE=1 but \"{string.Join(' ', Launcher)}\" does not run");
        Console.Error.WriteLine($"conformance skipped: \"{string.Join(' ', Launcher)}\" is not available");
        return false;
    }

    [Theory]
    [MemberData(nameof(GroupIndexes))]
    public async Task Group_matches_in_process_guard(int index)
    {
        if (!Available()) return;
        var group = ConformanceCorpus.Groups[index];
        var file = Path.Combine(Path.GetTempPath(), $"sqlguard-conf-{Guid.NewGuid():N}.json");
        File.WriteAllText(file, group.GetProperty("policy").GetRawText());
        using var proc = Launch(["serve", "--policy", file, "--port", "0"]);
        try
        {
            var tcs = new TaskCompletionSource<string>();
            var err = new System.Text.StringBuilder();
            proc.ErrorDataReceived += (_, e) =>
            {
                if (e.Data is null) return;
                err.AppendLine(e.Data);
                var m = Regex.Match(e.Data, @"listening on (http://\S+)");
                if (m.Success) tcs.TrySetResult(m.Groups[1].Value);
            };
            proc.BeginErrorReadLine();
            proc.Exited += (_, _) => tcs.TrySetException(new Exception($"server exited: {err}"));
            proc.EnableRaisingEvents = true;
            using var client = new SqlGuardClient(await tcs.Task.WaitAsync(TimeSpan.FromSeconds(60)));
            await ConformanceCorpus.AssertGroupAsync(client, group);
        }
        finally { try { proc.Kill(); } catch { } File.Delete(file); }
    }
}
