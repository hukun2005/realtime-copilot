using System.Text.Json;
using RealtimeCopilot.AudioCapture.Control;
using RealtimeCopilot.AudioCapture.Protocol;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class ControlProtocolTests
{
    [Fact]
    public void StatusReport_SerializesCamelCaseFields()
    {
        var report = new AudioStatusReport(
            "capturing",
            "loopback",
            "{0.0.0.00000000}.{guid}",
            "loopback",
            1_700_000_000_000);

        var json = ProtocolCodec.Serialize(ProtocolCodec.Create("audio.status", report));
        using var document = JsonDocument.Parse(json);
        var payload = document.RootElement.GetProperty("payload");

        Assert.Equal("capturing", payload.GetProperty("state").GetString());
        Assert.Equal("loopback", payload.GetProperty("source").GetString());
        Assert.Equal("loopback", payload.GetProperty("mode").GetString());
        Assert.Equal(1_700_000_000_000, payload.GetProperty("since").GetInt64());
        Assert.False(payload.TryGetProperty("error", out _));
        Assert.False(payload.TryGetProperty("errorCode", out _));
    }

    [Fact]
    public void StatusReport_KeepsErrorCodeWhenFailed()
    {
        var report = new AudioStatusReport(
            "error",
            "loopback",
            null,
            "loopback",
            1_700_000_000_000,
            "the default output device changed",
            "device_changed");

        var json = ProtocolCodec.Serialize(ProtocolCodec.Create("audio.status", report));
        using var document = JsonDocument.Parse(json);
        var payload = document.RootElement.GetProperty("payload");

        Assert.Equal("error", payload.GetProperty("state").GetString());
        Assert.Equal("device_changed", payload.GetProperty("errorCode").GetString());
        Assert.Equal("the default output device changed", payload.GetProperty("error").GetString());
    }

    [Fact]
    public void Hello_IncludesTicketWhenPresent()
    {
        var hello = new HelloPayload("token-value-0123456789", "audio", "0.1.0", "audio-capture", "ticket-value");

        var json = ProtocolCodec.Serialize(ProtocolCodec.Create("hello", hello));
        using var document = JsonDocument.Parse(json);
        var payload = document.RootElement.GetProperty("payload");

        Assert.Equal("ticket-value", payload.GetProperty("ticket").GetString());
    }

    [Fact]
    public void Hello_OmitsTicketWhenAbsent()
    {
        var hello = new HelloPayload("token-value-0123456789", "audio", "0.1.0", "audio-capture");

        var json = ProtocolCodec.Serialize(ProtocolCodec.Create("hello", hello));
        using var document = JsonDocument.Parse(json);

        Assert.False(document.RootElement.GetProperty("payload").TryGetProperty("ticket", out _));
    }
}
