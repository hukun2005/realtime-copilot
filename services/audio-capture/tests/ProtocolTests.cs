using System.Text.Json.Nodes;
using RealtimeCopilot.AudioCapture.Protocol;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class ProtocolTests
{
    [Fact]
    public void Serialize_OmitsAbsentRequestId()
    {
        var envelope = ProtocolCodec.Create("ping", new HeartbeatPayload(null));
        var json = ProtocolCodec.Serialize(envelope);
        Assert.DoesNotContain("requestId", json, StringComparison.Ordinal);
        Assert.Contains("\"v\":1", json, StringComparison.Ordinal);
        Assert.Contains("\"type\":\"ping\"", json, StringComparison.Ordinal);
    }

    [Fact]
    public void GeneratedIdsMatchSchemaPattern()
    {
        var eventId = ProtocolCodec.NewEventId();
        var requestId = ProtocolCodec.NewRequestId();
        Assert.Matches("^evt_[A-Za-z0-9_-]{8,64}$", eventId);
        Assert.Matches("^req_[A-Za-z0-9_-]{8,64}$", requestId);
    }

    [Fact]
    public void EnvelopeRoundTripsThroughJson()
    {
        var original = ProtocolCodec.Create(
            "system.error",
            new SystemErrorPayload("auth_failed", "invalid token", false),
            ProtocolCodec.NewRequestId());

        var json = ProtocolCodec.Serialize(original);
        var parsed = ProtocolCodec.DeserializeEnvelope(json);
        Assert.NotNull(parsed);
        Assert.Equal("system.error", parsed!.Type);
        Assert.Equal(1, parsed.V);
        Assert.Equal(original.Ts, parsed.Ts);

        var payload = ProtocolCodec.ReadPayload<SystemErrorPayload>(parsed);
        Assert.NotNull(payload);
        Assert.Equal("auth_failed", payload!.Code);
        Assert.False(payload.Retryable);
    }

    [Theory]
    [InlineData("hello")]
    [InlineData("system.ready")]
    [InlineData("system.error")]
    [InlineData("ping")]
    [InlineData("pong")]
    [InlineData("audio.status")]
    [InlineData("capture.start")]
    [InlineData("capture.stop")]
    public void FixturesSurviveRoundTripWithoutFieldLoss(string eventType)
    {
        var path = Path.Combine(ProtocolFixtures.Directory, eventType + ".json");
        var original = File.ReadAllText(path);

        var envelope = ProtocolCodec.DeserializeEnvelope(original);
        Assert.NotNull(envelope);
        Assert.Equal(eventType, envelope!.Type);

        var reserialized = ProtocolCodec.Serialize(envelope);
        Assert.True(
            JsonNode.DeepEquals(Normalize(original), Normalize(reserialized)),
            $"{eventType} lost fields:\noriginal={original}\nreserialized={reserialized}");
    }

    [Fact]
    public void UnknownEventSurvivesParsing()
    {
        var json = File.ReadAllText(Path.Combine(ProtocolFixtures.Directory, "invalid", "unknown-type.json"));
        var envelope = ProtocolCodec.DeserializeEnvelope(json);
        Assert.NotNull(envelope);
        Assert.Equal("meeting.summary", envelope!.Type);
    }

    private static JsonNode? Normalize(string json)
    {
        var node = JsonNode.Parse(json);
        return StripNulls(node);
    }

    private static JsonNode? StripNulls(JsonNode? node)
    {
        switch (node)
        {
            case JsonObject obj:
                var result = new JsonObject();
                foreach (var property in obj)
                {
                    if (property.Value is null)
                    {
                        continue;
                    }

                    result[property.Key] = StripNulls(property.Value);
                }

                return result;
            case JsonArray array:
                var items = new JsonArray();
                foreach (var item in array)
                {
                    items.Add(StripNulls(item));
                }

                return items;
            default:
                return node?.DeepClone();
        }
    }
}
