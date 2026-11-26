// Runs conformance/cases.json (the attack corpus) against a real `sqlguardllm serve`.
// Passes with a warning when no server executable exists, unless SQLGUARD_REQUIRE_CONFORMANCE=1.
// SQLGUARD_SERVER_CMD overrides the launcher (default: uv run --project <repo> --extra serve sqlguardllm).
using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;
using SqlGuardLlm;
using Xunit;

public class ConformanceTests
{
    private static readonly string Root = FindRoot();
    private static readonly bool Required = Environment.GetEnvironmentVariable("SQLGUARD_REQUIRE_CONFORMANCE") == "1";
    private static readonly string[] Launcher =
        (Environment.GetEnvironmentVariable("SQLGUARD_SERVER_CMD") ?? $"uv run --project {Root} --extra serve sqlguardllm").Split(' ');
    private static readonly JsonElement Groups =
        JsonDocument.Parse(File.ReadAllText(Path.Combine(Root, "conformance", "cases.json"))).RootElement.GetProperty("groups");

    private static string FindRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "conformance", "cases.json"))) dir = dir.Parent;
        return dir?.FullName ?? throw new InvalidOperationException("conformance/cases.json not found");
    }

    public static IEnumerable<object[]> GroupIndexes() => Enumerable.Range(0, Groups.GetArrayLength()).Select(i => new object[] { i });

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
        if (Required) throw new InvalidOperationException($"SQLGUARD_REQUIRE_CONFORMANCE=1 but \"{string.Join(' ', Launcher)}\" does not run");
        Console.Error.WriteLine($"conformance skipped: \"{string.Join(' ', Launcher)}\" is not available");
        return false;
    }

    [Theory]
    [MemberData(nameof(GroupIndexes))]
    public async Task Group_matches_in_process_guard(int index)
    {
        if (!Available()) return;
        var group = Groups[index];
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
            foreach (var c in group.GetProperty("cases").EnumerateArray())
            {
                var id = c.GetProperty("id").GetString();
                var ctx = c.GetProperty("context").EnumerateObject().ToDictionary(p => p.Name, p => (object?)p.Value.Clone());
                var r = await client.CheckAsync(c.GetProperty("sql").GetString()!, ctx);
                var exp = c.GetProperty("expected");
                Assert.True(exp.GetProperty("ok").GetBoolean() == r.Ok, $"{id}: ok");
                Assert.True(c.GetProperty("expect").GetString() == "allow" == r.Ok, $"{id}: corpus expectation");
                var expSql = exp.GetProperty("sql").ValueKind == JsonValueKind.Null ? null : exp.GetProperty("sql").GetString();
                Assert.True(expSql == r.Sql, $"{id}: sql {r.Sql}");
                var codes = r.Violations.Select(v => v.Code).Distinct().OrderBy(x => x, StringComparer.Ordinal).ToArray();
                var expCodes = exp.GetProperty("codes").EnumerateArray().Select(x => x.GetString()!).ToArray();
                Assert.True(expCodes.SequenceEqual(codes), $"{id}: codes {string.Join(',', codes)}");
            }
        }
        finally { try { proc.Kill(); } catch { } File.Delete(file); }
    }
}
