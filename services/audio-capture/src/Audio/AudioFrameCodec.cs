using System.Buffers.Binary;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 音频帧 v1 的序列化与校验。错误码与 Python 端 <c>copilot_core.audio.frame</c> 完全一致，
/// 由共享 fixtures 与端到端测试约束。
/// </summary>
public static class AudioFrameCodec
{
    /// <summary>协议版本。</summary>
    public const byte ProtocolVersion = 1;

    /// <summary>头部字节数。</summary>
    public const int HeaderBytes = 28;

    /// <summary>PCM 负载字节数（16000 × 0.02 × 2）。</summary>
    public const int PayloadBytes = 640;

    /// <summary>整帧字节数。</summary>
    public const int FrameBytes = HeaderBytes + PayloadBytes;

    /// <summary>采样率。</summary>
    public const uint SampleRate = 16000;

    /// <summary>声道数。</summary>
    public const ushort Channels = 1;

    /// <summary>PCM16LE 格式码。</summary>
    public const ushort SampleFormatPcm16 = 1;

    /// <summary>帧时长（毫秒）。</summary>
    public const ushort FrameDurationMs = 20;

    /// <summary>loopback 来源码。</summary>
    public const byte SourceLoopback = 0;

    /// <summary>mic 来源码。</summary>
    public const byte SourceMic = 1;

    /// <summary>把一帧编码为 668 字节。</summary>
    /// <param name="frame">待编码的帧。</param>
    /// <returns>小端字节序列。</returns>
    /// <exception cref="ArgumentException">负载长度不是 640 字节。</exception>
    public static byte[] Serialize(AudioFrame frame)
    {
        if (frame.Payload.Length != PayloadBytes)
        {
            throw new ArgumentException($"payload must be {PayloadBytes} bytes, got {frame.Payload.Length}", nameof(frame));
        }

        var buffer = new byte[FrameBytes];
        var span = buffer.AsSpan();

        span[0] = frame.ProtocolVersion;
        span[1] = frame.Source;
        BinaryPrimitives.WriteUInt16LittleEndian(span[2..], frame.FrameDurationMs);
        BinaryPrimitives.WriteUInt32LittleEndian(span[4..], frame.Sequence);
        BinaryPrimitives.WriteUInt64LittleEndian(span[8..], frame.CaptureTimestampNs);
        BinaryPrimitives.WriteUInt32LittleEndian(span[16..], frame.SampleRate);
        BinaryPrimitives.WriteUInt16LittleEndian(span[20..], frame.Channels);
        BinaryPrimitives.WriteUInt16LittleEndian(span[22..], frame.SampleFormat);
        BinaryPrimitives.WriteUInt32LittleEndian(span[24..], (uint)frame.Payload.Length);
        frame.Payload.Span.CopyTo(span[HeaderBytes..]);

        return buffer;
    }

    /// <summary>解析并校验一帧。</summary>
    /// <param name="data">完整的二进制消息。</param>
    /// <param name="frame">解析成功时的帧。</param>
    /// <param name="errorCode">失败时的稳定错误码。</param>
    /// <returns>是否解析成功。</returns>
    public static bool TryParse(ReadOnlySpan<byte> data, out AudioFrame frame, out string errorCode)
    {
        frame = default;
        errorCode = string.Empty;

        if (data.Length < HeaderBytes)
        {
            errorCode = "truncated_header";
            return false;
        }

        var version = data[0];
        var source = data[1];
        var durationMs = BinaryPrimitives.ReadUInt16LittleEndian(data[2..]);
        var sequence = BinaryPrimitives.ReadUInt32LittleEndian(data[4..]);
        var captureTsNs = BinaryPrimitives.ReadUInt64LittleEndian(data[8..]);
        var sampleRate = BinaryPrimitives.ReadUInt32LittleEndian(data[16..]);
        var channels = BinaryPrimitives.ReadUInt16LittleEndian(data[20..]);
        var sampleFormat = BinaryPrimitives.ReadUInt16LittleEndian(data[22..]);
        var payloadLength = BinaryPrimitives.ReadUInt32LittleEndian(data[24..]);

        if (version != ProtocolVersion)
        {
            errorCode = "unsupported_version";
            return false;
        }

        if (source is not (SourceLoopback or SourceMic))
        {
            errorCode = "invalid_source";
            return false;
        }

        if (durationMs != FrameDurationMs)
        {
            errorCode = "invalid_frame_duration";
            return false;
        }

        if (sampleRate != SampleRate)
        {
            errorCode = "invalid_sample_rate";
            return false;
        }

        if (channels != Channels)
        {
            errorCode = "invalid_channels";
            return false;
        }

        if (sampleFormat != SampleFormatPcm16)
        {
            errorCode = "invalid_sample_format";
            return false;
        }

        var actualPayload = data.Length - HeaderBytes;
        if (payloadLength != actualPayload)
        {
            errorCode = "payload_length_mismatch";
            return false;
        }

        if (payloadLength != PayloadBytes)
        {
            errorCode = "invalid_payload_size";
            return false;
        }

        frame = new AudioFrame(
            version,
            source,
            durationMs,
            sequence,
            captureTsNs,
            sampleRate,
            channels,
            sampleFormat,
            data[HeaderBytes..].ToArray());

        return true;
    }
}
