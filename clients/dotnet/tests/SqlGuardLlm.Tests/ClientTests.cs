using System.Net;
using System.Text;
using System.Text.Json;
using SqlGuardLlm;
using Xunit;

public class ClientTests
{
    private sealed class Fake(Func<HttpRequestMessage, string, Task<HttpResponseMessage>> f) : HttpMessageHandler
    {
        public HttpRequestMessage? Last; public string? Body;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken ct)
        { Last = r; Body = r.Content is null ? null : await r.Content.ReadAsStringAsync(ct); return await f(r, Body ?? "").WaitAsync(ct); }
    }
    private static HttpResponseMessage J(HttpStatusCode s, string j) =>
        new(s) { Content = new StringContent(j, Encoding.UTF8, "application/json") };
    private static (SqlGuardClient, Fake) Make(Func<HttpRequestMessage, string, Task<HttpResponseMessage>> f, string? token = null, TimeSpan? timeout = null)
    {
        var h = new Fake(f);
        return (new SqlGuardClient(new HttpClient(h), new SqlGuardOptions { BaseUrl = new("http://sg.test"), Token = token, Timeout = timeout ?? TimeSpan.FromSeconds(5) }), h);
    }
    private const string Ok = """{"ok":true,"sql":"SELECT 1 LIMIT 10","tables":["t"],"applied_filters":["tenant = 1"],"violations":[]}""";
    private const string Bad = """{"ok":false,"sql":null,"tables":[],"applied_filters":[],"violations":[{"code":"write_operation","message":"no","detail":"DROP"}]}""";

    [Fact] public async Task Check_ok_parses_and_sends_body_and_bearer()
    {
        var (c, h) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, Ok)), "tok");
        var r = await c.CheckAsync("SELECT 1", new Dictionary<string, object?> { ["tenant_id"] = 1 });
        Assert.True(r.Ok); Assert.Equal("SELECT 1 LIMIT 10", r.Sql);
        Assert.Equal(["t"], r.Tables); Assert.Equal(["tenant = 1"], r.AppliedFilters);
        Assert.Equal("/v1/check", h.Last!.RequestUri!.AbsolutePath);
        Assert.Equal("Bearer", h.Last.Headers.Authorization!.Scheme); Assert.Equal("tok", h.Last.Headers.Authorization.Parameter);
        var d = JsonDocument.Parse(h.Body!).RootElement;
        Assert.Equal("SELECT 1", d.GetProperty("sql").GetString()); Assert.Equal(1, d.GetProperty("context").GetProperty("tenant_id").GetInt32());
    }
    [Fact] public async Task No_token_means_no_auth_header_and_null_context()
    {
        var (c, h) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, Ok)));
        await c.CheckAsync("SELECT 1");
        Assert.Null(h.Last!.Headers.Authorization);
        Assert.Equal(JsonValueKind.Null, JsonDocument.Parse(h.Body!).RootElement.GetProperty("context").ValueKind);
    }
    [Fact] public async Task Rejected_query_is_200_ok_false()
    {
        var (c, _) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, Bad)));
        var r = await c.CheckAsync("DROP TABLE t");
        Assert.False(r.Ok); Assert.Equal("write_operation", r.Violations[0].Code);
    }
    [Fact] public async Task Enforce_returns_sql_or_throws_with_violations()
    {
        var (c, _) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, Ok)));
        Assert.Equal("SELECT 1 LIMIT 10", await c.EnforceAsync("SELECT 1"));
        var (c2, _) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, Bad)));
        var e = await Assert.ThrowsAsync<GuardViolationException>(() => c2.EnforceAsync("DROP TABLE t"));
        Assert.Equal("write_operation", e.Violations[0].Code);
    }
    [Fact] public async Task Explain_exposes_known_fields_and_raw()
    {
        var (c, h) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, """{"ok":true,"sql":"S","tables":["a"],"violations":[],"extra":{"x":1}}""")));
        var r = await c.ExplainAsync("S");
        Assert.True(r.Ok); Assert.Equal(["a"], r.Tables); Assert.Equal(1, r.Raw.GetProperty("extra").GetProperty("x").GetInt32());
        Assert.Equal("/v1/explain", h.Last!.RequestUri!.AbsolutePath);
    }
    [Fact] public async Task Health()
    {
        var (c, h) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, """{"status":"ok","version":"0.5.0"}""")));
        var r = await c.HealthAsync();
        Assert.Equal("ok", r.Status); Assert.Equal("0.5.0", r.Version); Assert.Equal(HttpMethod.Get, h.Last!.Method);
    }
    [Theory] [InlineData(400)] [InlineData(401)] [InlineData(404)] [InlineData(405)] [InlineData(413)]
    public async Task Http_errors_throw_with_server_code_and_no_secrets(int status)
    {
        var (c, _) = Make((_, _) => Task.FromResult(J((HttpStatusCode)status, """{"error":{"code":"some_code","message":"boom"}}""")), "SECRET-TOKEN");
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => c.CheckAsync("SELECT 1", new Dictionary<string, object?> { ["u"] = "CTX-SECRET" }));
        Assert.Equal("some_code", e.Code); Assert.Equal(status, e.StatusCode);
        Assert.DoesNotContain("SECRET", e.ToString());
    }
    [Fact] public async Task Unreachable_fails_closed()
    {
        var (c, _) = Make((_, _) => throw new HttpRequestException("conn refused SECRET-TOKEN"), "SECRET-TOKEN");
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => c.CheckAsync("SELECT 1"));
        Assert.Equal("unreachable", e.Code); Assert.DoesNotContain("SECRET", e.Message);
    }
    [Fact] public async Task Timeout_fails_closed()
    {
        var (c, _) = Make(async (_, _) => { await Task.Delay(5000); return J(HttpStatusCode.OK, Ok); }, null, TimeSpan.FromMilliseconds(50));
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => c.CheckAsync("SELECT 1"));
        Assert.Equal("timeout", e.Code);
    }
    [Fact] public async Task Caller_cancellation_is_not_a_timeout()
    {
        var (c, _) = Make(async (_, _) => { await Task.Delay(5000); return J(HttpStatusCode.OK, Ok); });
        using var cts = new CancellationTokenSource(50);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => c.CheckAsync("SELECT 1", null, cts.Token));
    }
    [Theory] [InlineData("not json")] [InlineData("null")] [InlineData("""{"ok":"yes"}""")] [InlineData("""{"ok":true,"sql":null}""")] [InlineData("[]")]
    public async Task Unexpected_body_fails_closed(string body)
    {
        var (c, _) = Make((_, _) => Task.FromResult(J(HttpStatusCode.OK, body)));
        await Assert.ThrowsAsync<SqlGuardException>(() => c.CheckAsync("SELECT 1"));
    }
    [Fact] public async Task Html_error_page_fails_closed()
    {
        var (c, _) = Make((_, _) => Task.FromResult(J(HttpStatusCode.BadGateway, "<html>bad gateway</html>")));
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => c.CheckAsync("SELECT 1"));
        Assert.Equal("http_502", e.Code);
    }
}
