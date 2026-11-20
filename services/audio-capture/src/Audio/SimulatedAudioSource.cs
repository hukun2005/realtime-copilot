using System.Diagnostics;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 模拟音频源：生成确定性的 16kHz 单声道 PCM16 帧，用于在没有 WASAPI 的阶段验证传输链路。
/// </summary>
/// <remarks>
/// 负载是固定 ramp（与协议 fixtures 相同算法），因此同一序号在任何机器上字节一致。
/// Phase 2-B 会用真实采集替换它，帧接口不变。
/// </remarks>
public sealed class SimulatedAudioSource
{
    private readonly byte[] _payload = new byte[AudioFrameCodec.PayloadBytes];
    private readonly byte _source;
    private uint _sequence;

    /// <summary>创建模拟源。</summary>
    /// <param name="source">来源码：<see cref="AudioFrameCodec.SourceLoopback"/> 或 <see cref="AudioFrameCodec.SourceMic"/>。</param>
    /// <param name="startSequence">起始序号。</param>
    public SimulatedAudioSource(byte source = AudioFrameCodec.SourceLoopback, uint startSequence = 0)
    {
        _source = source;
        _sequence = startSequence;
        for (var index = 0; index < _payload.Length; index++)
        {
            _payload[index] = (byte)(((index * 7) + 13) % 256);
        }
    }

    /// <summary>下一个待发送的序号。</summary>
    public uint NextSequence => _sequence;

    /// <summary>生成下一帧并递增序号。</summary>
    /// <param name="captureTimestampNs">采集侧单调时间戳（纳秒）。</param>
    /// <returns>可直接序列化的帧。</returns>
    public AudioFrame Next(ulong captureTimestampNs)
    {
        var frame = new AudioFrame(
            AudioFrameCodec.ProtocolVersion,
            _source,
            AudioFrameCodec.FrameDurationMs,
            _sequence,
            captureTimestampNs,
            AudioFrameCodec.SampleRate,
            AudioFrameCodec.Channels,
            AudioFrameCodec.SampleFormatPcm16,
            _payload);

        _sequence = unchecked(_sequence + 1);
        return frame;
    }
}

/// <summary>单调时钟：纳秒时间戳，只用于帧间隔与延迟统计。</summary>
public static class MonotonicClock
{
    private static readonly long Origin = Stopwatch.GetTimestamp();

    /// <summary>自进程启动以来的纳秒数。</summary>
    /// <returns>单调递增的纳秒时间戳。</returns>
    public static ulong Nanoseconds() => (ulong)(Stopwatch.GetElapsedTime(Origin).Ticks * 100);
}
