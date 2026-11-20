using System.Text.Json;
using System.Text.Json.Serialization;

namespace RealtimeCopilot.AudioCapture.Protocol;

/// <summary>协议信封：所有事件共用 v/type/eventId/requestId/ts/payload。</summary>
/// <typeparam name="TPayload">该事件的 payload 类型。</typeparam>
public sealed record EventEnvelope<TPayload>(
    [property: JsonPropertyName("v")] int V,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("eventId")] string EventId,
    [property: JsonPropertyName("requestId")] string? RequestId,
    [property: JsonPropertyName("ts")] long Ts,
    [property: JsonPropertyName("payload")] TPayload Payload);

/// <summary>hello 事件载荷。</summary>
public sealed record HelloPayload(
    [property: JsonPropertyName("token")] string Token,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("clientVersion")] string ClientVersion,
    [property: JsonPropertyName("clientName")] string? ClientName,
    [property: JsonPropertyName("ticket")] string? Ticket = null);

/// <summary>system.ready 事件载荷。</summary>
public sealed record SystemReadyPayload(
    [property: JsonPropertyName("serverVersion")] string ServerVersion,
    [property: JsonPropertyName("protocol")] int Protocol,
    [property: JsonPropertyName("capabilities")] IReadOnlyList<string> Capabilities,
    [property: JsonPropertyName("sessionId")] string? SessionId);

/// <summary>system.error 事件载荷。</summary>
public sealed record SystemErrorPayload(
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("retryable")] bool Retryable);

/// <summary>ping / pong 事件载荷。</summary>
public sealed record HeartbeatPayload(
    [property: JsonPropertyName("nonce")] string? Nonce);

/// <summary>JSON 编解码与事件构造。</summary>
public static class ProtocolCodec
{
    /// <summary>当前协议版本。</summary>
    public const int Version = 1;

    /// <summary>序列化选项：省略 null 字段（可选字段允许缺省）。</summary>
    public static readonly JsonSerializerOptions Options = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>生成事件 ID，满足 schema 的 EventId 约束。</summary>
    /// <returns>形如 <c>evt_&lt;32 hex&gt;</c> 的标识。</returns>
    public static string NewEventId() => "evt_" + Guid.NewGuid().ToString("N");

    /// <summary>生成请求关联 ID。</summary>
    /// <returns>形如 <c>req_&lt;32 hex&gt;</c> 的标识。</returns>
    public static string NewRequestId() => "req_" + Guid.NewGuid().ToString("N");

    /// <summary>构造事件信封。</summary>
    /// <typeparam name="TPayload">载荷类型。</typeparam>
    /// <param name="type">事件类型。</param>
    /// <param name="payload">载荷。</param>
    /// <param name="requestId">可选请求关联 ID。</param>
    /// <returns>可直接序列化的信封。</returns>
    public static EventEnvelope<TPayload> Create<TPayload>(string type, TPayload payload, string? requestId = null) =>
        new(Version, type, NewEventId(), requestId, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), payload);

    /// <summary>序列化信封。</summary>
    /// <typeparam name="TPayload">载荷类型。</typeparam>
    /// <param name="envelope">信封。</param>
    /// <returns>JSON 文本。</returns>
    public static string Serialize<TPayload>(EventEnvelope<TPayload> envelope) =>
        JsonSerializer.Serialize(envelope, Options);

    /// <summary>解析信封，payload 保持为原始 JSON。</summary>
    /// <param name="json">JSON 文本。</param>
    /// <returns>信封；格式非法时为 <c>null</c>。</returns>
    public static EventEnvelope<JsonElement>? DeserializeEnvelope(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<EventEnvelope<JsonElement>>(json, Options);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>把信封的 payload 反序列化为具体类型。</summary>
    /// <typeparam name="TPayload">目标类型。</typeparam>
    /// <param name="envelope">含原始 JSON payload 的信封。</param>
    /// <returns>载荷；不匹配时为 <c>null</c>。</returns>
    public static TPayload? ReadPayload<TPayload>(EventEnvelope<JsonElement> envelope)
        where TPayload : class
    {
        try
        {
            return envelope.Payload.Deserialize<TPayload>(Options);
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
