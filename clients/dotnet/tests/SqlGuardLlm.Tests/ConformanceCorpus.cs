// The attack corpus (conformance/cases.json) shared by the remote and the local conformance tests.
using System.Text.Json;
using SqlGuardLlm;
using Xunit;

internal static class ConformanceCorpus
{
    public static readonly string Root = FindRoot();
    public static readonly bool Required = Environment.GetEnvironmentVariable("SQLGUARD_REQUIRE_CONFORMANCE") == "1";
    public static readonly JsonElement Groups =
        JsonDocument.Parse(File.ReadAllText(Path.Combine(Root, "conformance", "cases.json"))).RootElement.GetProperty("groups");

    private static string FindRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "conformance", "cases.json"))) dir = dir.Parent;
        return dir?.FullName ?? throw new InvalidOperationException("conformance/cases.json not found");
    }

    public static IEnumerable<object[]> GroupIndexes() => Enumerable.Range(0, Groups.GetArrayLength()).Select(i => new object[] { i });

    /// <summary>Runs every case of the group through <paramref name="guard"/> and compares ok, SQL and violation codes.</summary>
    public static async Task AssertGroupAsync(ISqlGuard guard, JsonElement group)
    {
        foreach (var c in group.GetProperty("cases").EnumerateArray())
        {
            var id = c.GetProperty("id").GetString();
            var ctx = c.GetProperty("context").EnumerateObject().ToDictionary(p => p.Name, p => (object?)p.Value.Clone());
            var r = await guard.CheckAsync(c.GetProperty("sql").GetString()!, ctx);
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
}
