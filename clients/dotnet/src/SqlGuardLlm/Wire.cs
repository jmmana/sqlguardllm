using System.Buffers;
using System.Collections;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace SqlGuardLlm;

/// <summary>Source-generated metadata: no reflection, so trimming and Native AOT stay clean.</summary>
[JsonSourceGenerationOptions(PropertyNameCaseInsensitive = true)]
[JsonSerializable(typeof(CheckResult))]
[JsonSerializable(typeof(HealthResult))]
[JsonSerializable(typeof(JsonElement))]
[JsonSerializable(typeof(List<string>))]
[JsonSerializable(typeof(List<Violation>))]
internal sealed partial class SqlGuardJsonContext : JsonSerializerContext;

/// <summary>Encoding of requests and decoding of results, shared by remote and local mode so both return the same types.</summary>
internal static class Wire
{
    private const int MaxDepth = 32;

    /// <summary>The HTTP body <c>{"sql": ..., "context": ...}</c>.</summary>
    public static byte[] HttpBody(string sql, IReadOnlyDictionary<string, object?>? context)
    {
        ArgumentNullException.ThrowIfNull(sql);
        var buffer = new ArrayBufferWriter<byte>();
        using (var w = new Utf8JsonWriter(buffer))
        {
            w.WriteStartObject();
            w.WriteString("sql", sql);
            w.WritePropertyName("context");
            WriteValue(w, context, 0);
            w.WriteEndObject();
        }
        return buffer.WrittenSpan.ToArray();
    }

    /// <summary>One stdio request line (without the newline). <paramref name="fields"/> writes the fields after <c>id</c> and <c>type</c>.</summary>
    public static byte[] EngineRequest(long id, string type, Action<Utf8JsonWriter> fields)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var w = new Utf8JsonWriter(buffer))
        {
            w.WriteStartObject();
            w.WriteNumber("id", id);
            w.WriteString("type", type);
            fields(w);
            w.WriteEndObject();
        }
        return buffer.WrittenSpan.ToArray();
    }

    public static CheckResult ParseCheck(JsonElement body, string source)
    {
        CheckResult? r;
        try { r = body.ValueKind == JsonValueKind.Object ? body.Deserialize(SqlGuardJsonContext.Default.CheckResult) : null; }
        catch (JsonException e) { throw new SqlGuardException("bad_response", $"{source} answered something unexpected.", null, e); }
        if (r is null) throw new SqlGuardException("bad_response", $"{source} answered check without a result.");
        if (r.Ok && string.IsNullOrEmpty(r.Sql)) throw new SqlGuardException("bad_response", $"{source} answered ok without SQL; treating as failure.");
        return r;
    }

    public static ExplainResult ParseExplain(JsonElement raw)
    {
        if (raw.ValueKind != JsonValueKind.Object) throw new SqlGuardException("bad_response", "Unexpected explain response.");
        try
        {
            var ok = raw.TryGetProperty("ok", out var o) && o.ValueKind == JsonValueKind.True;
            var s = raw.TryGetProperty("sql", out var sq) && sq.ValueKind == JsonValueKind.String ? sq.GetString() : null;
            var t = raw.TryGetProperty("tables", out var tb) && tb.ValueKind == JsonValueKind.Array
                ? tb.Deserialize(SqlGuardJsonContext.Default.ListString)! : new List<string>();
            var v = raw.TryGetProperty("violations", out var vi) && vi.ValueKind == JsonValueKind.Array
                ? vi.Deserialize(SqlGuardJsonContext.Default.ListViolation)! : new List<Violation>();
            return new ExplainResult(ok, s, t, v, raw.Clone());
        }
        catch (JsonException e) { throw new SqlGuardException("bad_response", "Unexpected explain response.", null, e); }
    }

    /// <summary>Writes a context value without reflection. Unsupported types fail loudly instead of being dropped.</summary>
    public static void WriteValue(Utf8JsonWriter w, object? value, int depth)
    {
        if (depth > MaxDepth) throw new ArgumentException("context is nested too deeply");
        switch (value)
        {
            case null: w.WriteNullValue(); break;
            case string s: w.WriteStringValue(s); break;
            case bool b: w.WriteBooleanValue(b); break;
            case int i: w.WriteNumberValue(i); break;
            case long l: w.WriteNumberValue(l); break;
            case short sh: w.WriteNumberValue(sh); break;
            case byte by: w.WriteNumberValue(by); break;
            case uint ui: w.WriteNumberValue(ui); break;
            case ulong ul: w.WriteNumberValue(ul); break;
            case float f: w.WriteNumberValue(f); break;
            case double d: w.WriteNumberValue(d); break;
            case decimal m: w.WriteNumberValue(m); break;
            case Guid g: w.WriteStringValue(g); break;
            case DateTime dt: w.WriteStringValue(dt); break;
            case DateTimeOffset dto: w.WriteStringValue(dto); break;
            case JsonElement e:
                if (e.ValueKind == JsonValueKind.Undefined) throw new ArgumentException("context contains an undefined JsonElement");
                e.WriteTo(w);
                break;
            case IReadOnlyDictionary<string, object?> dict:
                w.WriteStartObject();
                foreach (var kv in dict) { w.WritePropertyName(kv.Key); WriteValue(w, kv.Value, depth + 1); }
                w.WriteEndObject();
                break;
            case IEnumerable seq:
                w.WriteStartArray();
                foreach (var item in seq) WriteValue(w, item, depth + 1);
                w.WriteEndArray();
                break;
            default:
                throw new ArgumentException($"context value of type {value.GetType().Name} is not supported; use strings, numbers, booleans, null, dictionaries, lists or JsonElement");
        }
    }
}
