using System.Text.Json;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class AudioFrameCodecTests
{
    private static string FixturePath(string name) =>
        Path.Combine(ProtocolFixtures.Directory, "audio", name);

    [Fact]
    public void Serialize_MatchesCrossLanguageFixture()
    {
        var source = new SimulatedAudioSource(startSequence: 1);
        var frame = source.Next(1_000_000_000);
        var bytes = AudioFrameCodec.Serialize(frame);

        var expected = File.ReadAllBytes(FixturePath("valid-frame.bin"));
        Assert.Equal(expected.Length, bytes.Length);
        Assert.Equal(expected, bytes);
    }

    [Fact]
    public void Serialize_UsesLittleEndianOffsets()
    {
        var frame = new AudioFrame(
            AudioFrameCodec.ProtocolVersion,
            AudioFrameCodec.SourceLoopback,
            AudioFrameCodec.FrameDurationMs,
            Sequence: 0x01020304,
            CaptureTimestampNs: 0x0102030405060708,
            AudioFrameCodec.SampleRate,
            AudioFrameCodec.Channels,
            AudioFrameCodec.SampleFormatPcm16,
            new byte[AudioFrameCodec.PayloadBytes]);

        var bytes = AudioFrameCodec.Serialize(frame);

        Assert.Equal(1, bytes[0]);
        Assert.Equal(0, bytes[1]);
        Assert.Equal(20, bytes[2]);
        Assert.Equal(0, bytes[3]);
        Assert.Equal(new byte[] { 0x04, 0x03, 0x02, 0x01 }, bytes[4..8]);
        Assert.Equal(new byte[] { 0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01 }, bytes[8..16]);
        Assert.Equal(new byte[] { 0x80, 0x3E, 0x00, 0x00 }, bytes[16..20]);
        Assert.Equal(new byte[] { 0x01, 0x00 }, bytes[20..22]);
        Assert.Equal(new byte[] { 0x01, 0x00 }, bytes[22..24]);
        Assert.Equal(new byte[] { 0x80, 0x02, 0x00, 0x00 }, bytes[24..28]);
        Assert.Equal(AudioFrameCodec.FrameBytes, bytes.Length);
    }

    [Fact]
    public void TryParse_ReadsFixtureFields()
    {
        var data = File.ReadAllBytes(FixturePath("valid-frame.bin"));
        var meta = JsonDocument.Parse(File.ReadAllText(FixturePath("valid-frame.meta.json"))).RootElement;

        Assert.True(AudioFrameCodec.TryParse(data, out var frame, out var error), error);
        Assert.Equal(meta.GetProperty("protocolVersion").GetInt32(), frame.ProtocolVersion);
        Assert.Equal(AudioFrameCodec.SourceLoopback, frame.Source);
        Assert.Equal(meta.GetProperty("frameDurationMs").GetInt32(), frame.FrameDurationMs);
        Assert.Equal(meta.GetProperty("sequence").GetUInt32(), frame.Sequence);
        Assert.Equal(meta.GetProperty("captureTsNs").GetUInt64(), frame.CaptureTimestampNs);
        Assert.Equal(meta.GetProperty("sampleRate").GetUInt32(), frame.SampleRate);
        Assert.Equal(meta.GetProperty("channels").GetInt32(), frame.Channels);
        Assert.Equal(meta.GetProperty("sampleFormat").GetInt32(), frame.SampleFormat);
        Assert.Equal(meta.GetProperty("payloadLength").GetInt32(), frame.Payload.Length);
    }

    [Fact]
    public void Serialize_RejectsWrongPayloadLength()
    {
        var frame = new AudioFrame(1, 0, 20, 1, 1, 16000, 1, 1, new byte[320]);
        Assert.Throws<ArgumentException>(() => AudioFrameCodec.Serialize(frame));
    }

    [Theory]
    [InlineData("invalid-version.bin", "unsupported_version")]
    [InlineData("invalid-rate.bin", "invalid_sample_rate")]
    [InlineData("invalid-channels.bin", "invalid_channels")]
    [InlineData("invalid-format.bin", "invalid_sample_format")]
    [InlineData("truncated-header.bin", "truncated_header")]
    [InlineData("payload-mismatch.bin", "payload_length_mismatch")]
    public void TryParse_ReportsSharedErrorCodes(string fixture, string expectedCode)
    {
        var data = File.ReadAllBytes(FixturePath(fixture));
        Assert.False(AudioFrameCodec.TryParse(data, out _, out var errorCode));
        Assert.Equal(expectedCode, errorCode);
    }

    [Fact]
    public void TryParse_RejectsUnknownSource()
    {
        var data = File.ReadAllBytes(FixturePath("valid-frame.bin"));
        data[1] = 9;

        Assert.False(AudioFrameCodec.TryParse(data, out _, out var errorCode));
        Assert.Equal("invalid_source", errorCode);
    }

    [Fact]
    public void TryParse_RejectsWrongDuration()
    {
        var data = File.ReadAllBytes(FixturePath("valid-frame.bin"));
        data[2] = 40;

        Assert.False(AudioFrameCodec.TryParse(data, out _, out var errorCode));
        Assert.Equal("invalid_frame_duration", errorCode);
    }
}
