using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class Pcm16Tests
{
    [Fact]
    public void FromFloat_WritesLittleEndianSamples()
    {
        var destination = new byte[8];
        var written = Pcm16.FromFloat([0f, 1f, -1f, 0.5f], destination);

        Assert.Equal(8, written);
        Assert.Equal(new byte[] { 0x00, 0x00 }, destination[0..2]);
        Assert.Equal(new byte[] { 0xFF, 0x7F }, destination[2..4]);
        Assert.Equal(new byte[] { 0x00, 0x80 }, destination[4..6]);
        Assert.Equal(new byte[] { 0x00, 0x40 }, destination[6..8]);
    }

    [Fact]
    public void FromFloat_ClampsOutOfRangeValues()
    {
        var destination = new byte[4];
        Pcm16.FromFloat([2f, -2f], destination);

        Assert.Equal(short.MaxValue, BitConverter.ToInt16(destination, 0));
        Assert.Equal(short.MinValue, BitConverter.ToInt16(destination, 2));
    }

    [Fact]
    public void ToBytes_AllocatesExactLength()
    {
        var bytes = Pcm16.ToBytes([0f, 0f, 0f]);
        Assert.Equal(6, bytes.Length);
        Assert.All(bytes, value => Assert.Equal(0, value));
    }

    [Fact]
    public void FromFloat_RejectsSmallDestination()
    {
        Assert.Throws<ArgumentException>(() => Pcm16.FromFloat([0f, 0f], new byte[2]));
    }

    [Fact]
    public void FromFloat_ProducesSilenceForZero()
    {
        var bytes = Pcm16.ToBytes(new float[320]);
        Assert.Equal(AudioFrameCodec.PayloadBytes, bytes.Length);
        Assert.All(bytes, value => Assert.Equal(0, value));
    }
}
