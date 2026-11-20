namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 一帧 20ms 音频。布局见 <c>packages/protocol/audio-frame.v1.md</c>。
/// </summary>
/// <param name="ProtocolVersion">协议版本，当前固定 1。</param>
/// <param name="Source">0 = loopback，1 = mic。</param>
/// <param name="FrameDurationMs">帧时长，当前固定 20。</param>
/// <param name="Sequence">每个 source 流独立递增的序号，uint32 回绕。</param>
/// <param name="CaptureTimestampNs">采集侧单调时钟，纳秒。</param>
/// <param name="SampleRate">采样率，当前固定 16000。</param>
/// <param name="Channels">声道数，当前固定 1。</param>
/// <param name="SampleFormat">1 = PCM16LE。</param>
/// <param name="Payload">PCM 负载，固定 640 字节。</param>
public readonly record struct AudioFrame(
    byte ProtocolVersion,
    byte Source,
    ushort FrameDurationMs,
    uint Sequence,
    ulong CaptureTimestampNs,
    uint SampleRate,
    ushort Channels,
    ushort SampleFormat,
    ReadOnlyMemory<byte> Payload)
{
    /// <summary>整帧字节数（头 + 负载）。</summary>
    public int TotalBytes => AudioFrameCodec.HeaderBytes + Payload.Length;
}
