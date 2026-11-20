using System.Buffers.Binary;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>浮点采样到 PCM16LE 的转换。</summary>
public static class Pcm16
{
    /// <summary>把 [-1,1] 的浮点采样转换为小端 16 位整数。</summary>
    /// <param name="samples">源采样。</param>
    /// <param name="destination">目标缓冲，长度至少 <c>samples.Length * 2</c>。</param>
    /// <returns>写入的字节数。</returns>
    public static int FromFloat(ReadOnlySpan<float> samples, Span<byte> destination)
    {
        if (destination.Length < samples.Length * 2)
        {
            throw new ArgumentException("destination is too small", nameof(destination));
        }

        for (var index = 0; index < samples.Length; index++)
        {
            var value = samples[index];
            var scaled = value >= 1f ? short.MaxValue
                : value <= -1f ? short.MinValue
                : (short)Math.Round(value * short.MaxValue);

            BinaryPrimitives.WriteInt16LittleEndian(destination[(index * 2)..], scaled);
        }

        return samples.Length * 2;
    }

    /// <summary>把浮点采样转换为新的字节数组。</summary>
    /// <param name="samples">源采样。</param>
    /// <returns>PCM16LE 字节。</returns>
    public static byte[] ToBytes(ReadOnlySpan<float> samples)
    {
        var buffer = new byte[samples.Length * 2];
        FromFloat(samples, buffer);
        return buffer;
    }
}
