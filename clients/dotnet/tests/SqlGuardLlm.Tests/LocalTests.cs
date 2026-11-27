using System.Text;
using System.Text.Json;
using SqlGuardLlm;
using Xunit;

/// <summary>Local mode against fake engines written in sh (so not on Windows; the conformance run covers the real binary there).</summary>
public class LocalTests
{
    private const string Rid = "linux-x64";

    // Answers hello and load_policy; the id is read back from the request line.
    private const string Prelude = """
        #!/bin/sh
        reply() { echo "{\"id\":$1,\"type\":\"check\",\"result\":{\"ok\":true,\"sql\":\"$2\",\"tables\":[],\"applied_filters\":[],\"violations\":[]}}"; }
        handshake() {
          case "$1" in
            *'"type":"hello"'*) echo "{\"id\":$2,\"type\":\"hello\",\"protocol\":1,\"engine_version\":\"0.5.0a3\",\"dialects\":[]}" ;;
            *'"type":"load_policy"'*) echo "{\"id\":$2,\"type\":\"load_policy\",\"handle\":\"p1\"}" ;;
          esac
        }
        idof() { v=${1#\{\"id\":}; echo "${v%%,*}"; }
        sqlof() { v=${1#*\"sql\":\"}; echo "${v%%\"*}"; }
        """;

    private static Task<SqlGuardLocal> Start(string body, Action<SqlGuardLocalOptions>? tune = null)
    {
        var (app, _) = EngineTests.Fake((Prelude + "\n" + body + "\n").Replace("\r", ""));
        var options = new SqlGuardLocalOptions { PolicyJson = """{"dialect":"postgres"}""", RestartBackoff = TimeSpan.FromMilliseconds(100) };
        tune?.Invoke(options);
        return EngineTests.StartLocal(app, Rid, options: options);
    }

    // Echoes the SQL back; "crash" exits, "hang" never answers.
    private const string Echo = """
        while IFS= read -r line; do
          id=$(idof "$line")
          handshake "$line" "$id"
          case "$line" in
            *'"sql":"crash"'*) exit 1 ;;
            *'"sql":"hang"'*) ;;
            *'"type":"check"'*) reply "$id" "$(sqlof "$line")" ;;
            *'"type":"explain"'*) echo "{\"id\":$id,\"type\":\"explain\",\"result\":{\"ok\":false,\"sql\":null,\"tables\":[\"t\"],\"violations\":[{\"code\":\"c\",\"message\":\"m\"}],\"extra\":1}}" ;;
          esac
        done
        """;

    [Fact]
    public async Task Explain_returns_the_same_result_type_with_the_raw_json()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo);
        var r = await guard.ExplainAsync("SELECT 1");
        Assert.False(r.Ok);
        Assert.Equal(["t"], r.Tables);
        Assert.Equal("c", Assert.Single(r.Violations).Code);
        Assert.Equal(1, r.Raw.GetProperty("extra").GetInt32());
    }

    [Fact]
    public async Task Local_and_remote_clients_share_one_interface()
    {
        if (OperatingSystem.IsWindows()) return;
        await using ISqlGuard guard = await Start(Echo);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
        await using ISqlGuard remote = new SqlGuardClient("http://127.0.0.1:9");
        Assert.NotNull(remote);
    }

    [Fact]
    public async Task Concurrent_calls_get_their_own_answers_even_when_the_engine_answers_out_of_order()
    {
        if (OperatingSystem.IsWindows()) return;
        // After the handshake the engine reads requests in pairs and answers the second one first.
        await using var guard = await Start("""
            while IFS= read -r line; do
              handshake "$line" "$(idof "$line")"
              case "$line" in *'"type":"load_policy"'*) break ;; esac
            done
            while IFS= read -r a && IFS= read -r b; do
              reply "$(idof "$b")" "$(sqlof "$b")"
              reply "$(idof "$a")" "$(sqlof "$a")"
            done
            """);
        var calls = Enumerable.Range(0, 50).Select(i => Task.Run(async () => (i, await guard.EnforceAsync($"SELECT {i}")))).ToArray();
        foreach (var (i, sql) in await Task.WhenAll(calls)) Assert.Equal($"SELECT {i}", sql);
    }

    [Fact]
    public async Task A_crash_fails_pending_and_new_calls_then_restarts_with_backoff()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo);
        var hung = guard.CheckAsync("hang");
        await Task.Delay(300);
        var crash = Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("crash"));
        Assert.Equal("engine_closed", (await crash).Code);
        Assert.Equal("engine_closed", (await Assert.ThrowsAsync<SqlGuardException>(() => hung)).Code);

        var during = await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("SELECT 1"));
        Assert.Equal("engine_restarting", during.Code);   // fail closed while backing off

        await Task.Delay(300);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));   // a fresh engine
    }

    [Fact]
    public async Task Backoff_grows_on_consecutive_crashes_and_resets_after_a_served_call()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo, o => { o.RestartBackoff = TimeSpan.FromMilliseconds(200); o.MaxRestartBackoff = TimeSpan.FromSeconds(2); });
        await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("crash"));   // failure 1: wait 200 ms
        await Task.Delay(300);
        await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("crash"));   // failure 2: wait 400 ms
        await Task.Delay(300);
        Assert.Equal("engine_restarting", (await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("SELECT 1"))).Code);
        await Task.Delay(400);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
        await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("crash"));   // reset: wait 200 ms again
        await Task.Delay(300);
        Assert.Equal("SELECT 2", await guard.EnforceAsync("SELECT 2"));
    }

    [Fact]
    public async Task A_call_that_times_out_fails_and_the_engine_is_replaced()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo, o => o.Timeout = TimeSpan.FromMilliseconds(400));
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("hang"));
        Assert.Equal("timeout", e.Code);
        await Task.Delay(300);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
    }

    [Fact]
    public async Task Cancelling_one_call_does_not_disturb_the_others()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo);
        using var cts = new CancellationTokenSource(200);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => guard.CheckAsync("hang", ct: cts.Token));
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));   // same engine, pipe still in sync
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => guard.CheckAsync("SELECT 2", ct: new CancellationToken(true)));
    }

    [Fact]
    public async Task Disposing_fails_pending_calls_and_later_calls()
    {
        if (OperatingSystem.IsWindows()) return;
        var guard = await Start(Echo);
        var hung = guard.CheckAsync("hang");
        await Task.Delay(200);
        await guard.DisposeAsync();
        Assert.Equal("engine_closed", (await Assert.ThrowsAsync<SqlGuardException>(() => hung)).Code);
        await Assert.ThrowsAsync<ObjectDisposedException>(() => guard.CheckAsync("SELECT 1"));
        await guard.DisposeAsync();   // idempotent
    }

    [Fact]
    public async Task Garbage_and_unmatched_answers_fail_closed()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var garbage = await Start("""
            while IFS= read -r line; do
              id=$(idof "$line")
              handshake "$line" "$id"
              case "$line" in *'"type":"check"'*) echo "not json" ;; esac
            done
            """);
        Assert.Equal("bad_response", (await Assert.ThrowsAsync<SqlGuardException>(() => garbage.CheckAsync("SELECT 1"))).Code);

        await using var wrongType = await Start("""
            while IFS= read -r line; do
              id=$(idof "$line")
              handshake "$line" "$id"
              case "$line" in *'"type":"check"'*) echo "{\"id\":$id,\"type\":\"explain\",\"result\":{}}" ;; esac
            done
            """);
        Assert.Equal("bad_response", (await Assert.ThrowsAsync<SqlGuardException>(() => wrongType.CheckAsync("SELECT 1"))).Code);

        await using var noSql = await Start("""
            while IFS= read -r line; do
              id=$(idof "$line")
              handshake "$line" "$id"
              case "$line" in *'"type":"check"'*) echo "{\"id\":$id,\"type\":\"check\",\"result\":{\"ok\":true,\"violations\":[]}}" ;; esac
            done
            """);
        Assert.Equal("bad_response", (await Assert.ThrowsAsync<SqlGuardException>(() => noSql.CheckAsync("SELECT 1"))).Code);
    }

    [Fact]
    public async Task An_error_answer_throws_with_its_code_and_keeps_the_engine()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start("""
            while IFS= read -r line; do
              id=$(idof "$line")
              handshake "$line" "$id"
              case "$line" in
                *'"sql":"bad"'*) echo "{\"id\":$id,\"type\":\"error\",\"allowed\":false,\"error\":{\"code\":\"unknown_handle\",\"message\":\"x\"}}" ;;
                *'"type":"check"'*) reply "$id" "$(sqlof "$line")" ;;
              esac
            done
            """);
        Assert.Equal("unknown_handle", (await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync("bad"))).Code);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
    }

    [Fact]
    public async Task A_rejected_query_is_a_result_and_enforce_throws()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start("""
            while IFS= read -r line; do
              id=$(idof "$line")
              handshake "$line" "$id"
              case "$line" in *'"type":"check"'*) echo "{\"id\":$id,\"type\":\"check\",\"result\":{\"ok\":false,\"sql\":null,\"violations\":[{\"code\":\"write_not_allowed\",\"message\":\"m\"}]}}" ;; esac
            done
            """);
        Assert.False((await guard.CheckAsync("DELETE FROM t")).Ok);
        var e = await Assert.ThrowsAsync<GuardViolationException>(() => guard.EnforceAsync("DELETE FROM t"));
        Assert.Equal("write_not_allowed", e.Violations[0].Code);
    }

    [Fact]
    public async Task Oversized_requests_are_refused_without_hurting_the_engine()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo);
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => guard.CheckAsync(new string('a', 2 * 1024 * 1024)));
        Assert.Equal("message_too_large", e.Code);
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
    }

    [Fact]
    public async Task Unsupported_context_values_are_refused()
    {
        if (OperatingSystem.IsWindows()) return;
        await using var guard = await Start(Echo);
        await Assert.ThrowsAsync<ArgumentException>(() => guard.CheckAsync("SELECT 1", new Dictionary<string, object?> { ["x"] = new object() }));
        Assert.Equal("SELECT 1", await guard.EnforceAsync("SELECT 1"));
    }

    [Fact]
    public async Task Options_are_validated()
    {
        await Assert.ThrowsAsync<ArgumentException>(() => EngineTests.StartLocal("/nowhere", Rid, "[]"));
        await Assert.ThrowsAsync<ArgumentException>(() => EngineTests.StartLocal("/nowhere", Rid, "{oops"));
        await Assert.ThrowsAsync<ArgumentOutOfRangeException>(() => EngineTests.StartLocal("/nowhere", Rid, options: new SqlGuardLocalOptions { PolicyJson = "{}", Timeout = TimeSpan.Zero }));
    }

    [Fact]
    public async Task Public_entry_point_fails_closed_without_a_packaged_engine()
    {
        // The test output folder carries no runtimes/{rid}/native/sqlguardllm-engine.
        var e = await Assert.ThrowsAsync<SqlGuardException>(() => SqlGuard.CreateLocalAsync(new SqlGuardLocalOptions { PolicyJson = "{}" }));
        Assert.Equal("engine_unsupported", e.Code);
    }

    [Fact]
    public void Wire_encodes_context_without_reflection()
    {
        var ctx = new Dictionary<string, object?>
        {
            ["tenant"] = "a\nb",
            ["n"] = 7,
            ["big"] = 5_000_000_000L,
            ["f"] = 1.5,
            ["ok"] = true,
            ["none"] = null,
            ["list"] = new object?[] { 1, "x", null },
            ["names"] = new[] { "p", "q" },
            ["obj"] = new Dictionary<string, object?> { ["k"] = JsonDocument.Parse("""{"a":[1]}""").RootElement.Clone() },
        };
        var body = Encoding.UTF8.GetString(Wire.HttpBody("SELECT 1", ctx));
        Assert.Equal(
            """{"sql":"SELECT 1","context":{"tenant":"a\nb","n":7,"big":5000000000,"f":1.5,"ok":true,"none":null,"list":[1,"x",null],"names":["p","q"],"obj":{"k":{"a":[1]}}}}""",
            body);
        Assert.Equal("""{"sql":"x","context":null}""", Encoding.UTF8.GetString(Wire.HttpBody("x", null)));
        Assert.Throws<ArgumentException>(() => Wire.HttpBody("x", new Dictionary<string, object?> { ["d"] = new object() }));
    }
}
