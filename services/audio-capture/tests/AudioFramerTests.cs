using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class AudioFramerTests
{
    private static readonly byte[] Silence = new byte[AudioFrameCodec.PayloadBytes];

    [Fact]
    public void Append_EmitsFramesOnlyWhenFull()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceLoopback, 5_000_000_000);

        Assert.Empty(framer.Append(Silence.AsSpan(0, 320)));
        Assert.Equal(320, framer.PendingBytes);

        var frames = framer.Append(Silence.AsSpan(320));
        Assert.Single(frames);
        Assert.Equal(0, framer.PendingBytes);
        Assert.Equal(0u, frames[0].Sequence);
        Assert.Equal(5_000_000_000ul, frames[0].CaptureTimestampNs);
    }

    [Fact]
    public void Append_HandlesBlocksLargerThanOneFrame()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceLoopback, 0);
        var frames = framer.Append(new byte[AudioFrameCodec.PayloadBytes * 3 + 200]);

        Assert.Equal(3, frames.Count);
        Assert.Equal(200, framer.PendingBytes);
        Assert.Equal(new uint[] { 0, 1, 2 }, frames.Select(frame => frame.Sequence).ToArray());
    }

    [Fact]
    public void Append_AdvancesTimestampByFrameDuration()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceLoopback, 1_000_000_000);
        var frames = framer.Append(new byte[AudioFrameCodec.PayloadBytes * 2]);

        Assert.Equal(1_000_000_000ul, frames[0].CaptureTimestampNs);
        Assert.Equal(1_020_000_000ul, frames[1].CaptureTimestampNs);
    }

    [Fact]
    public void Append_SetsProtocolFields()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceMic, 0);
        var frame = framer.Append(Silence)[0];

        Assert.Equal(AudioFrameCodec.ProtocolVersion, frame.ProtocolVersion);
        Assert.Equal(AudioFrameCodec.SourceMic, frame.Source);
        Assert.Equal(AudioFrameCodec.FrameDurationMs, frame.FrameDurationMs);
        Assert.Equal(AudioFrameCodec.SampleRate, frame.SampleRate);
        Assert.Equal(AudioFrameCodec.Channels, frame.Channels);
        Assert.Equal(AudioFrameCodec.SampleFormatPcm16, frame.SampleFormat);
        Assert.Equal(AudioFrameCodec.PayloadBytes, frame.Payload.Length);
    }

    [Fact]
    public void Append_EmptyInputEmitsNothing()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceLoopback, 0);
        Assert.Empty(framer.Append(ReadOnlySpan<byte>.Empty));
    }

    [Fact]
    public void Constructor_RejectsUnknownSource()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new AudioFramer(9, 0));
    }

    [Fact]
    public void Frames_AreSerializableAsValidProtocolFrames()
    {
        var framer = new AudioFramer(AudioFrameCodec.SourceLoopback, 7_000_000_000);
        var frame = framer.Append(Silence)[0];

        var bytes = AudioFrameCodec.Serialize(frame);
        Assert.True(AudioFrameCodec.TryParse(bytes, out var parsed, out var error), error);
        Assert.Equal(frame.Sequence, parsed.Sequence);
        Assert.Equal(frame.CaptureTimestampNs, parsed.CaptureTimestampNs);
    }
}
