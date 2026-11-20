namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 把连续的 PCM16LE 字节切成固定 20ms（640 字节）的协议帧。
/// </summary>
/// <remarks>
/// 设备回调的块大小不受控，因此这里维护跨调用的残留字节；时间戳按"已发出的帧数 × 20ms"
/// 推进，而不是读系统时钟，保证同一流内单调且与采样数严格对应。
/// </remarks>
public sealed class AudioFramer
{
    private readonly byte _source;
    private readonly ulong _startTimestampNs;
    private readonly byte[] _pending = new byte[AudioFrameCodec.PayloadBytes];
    private int _pendingBytes;
    private uint _sequence;
    private ulong _framesEmitted;

    /// <summary>创建分帧器。</summary>
    /// <param name="source">来源码：loopback 或 mic。</param>
    /// <param name="startTimestampNs">首帧时间戳（单调时钟纳秒）。</param>
    /// <param name="startSequence">起始序号。</param>
    public AudioFramer(byte source, ulong startTimestampNs, uint startSequence = 0)
    {
        if (source is not (AudioFrameCodec.SourceLoopback or AudioFrameCodec.SourceMic))
        {
            throw new ArgumentOutOfRangeException(nameof(source), source, "unknown audio source");
        }

        _source = source;
        _startTimestampNs = startTimestampNs;
        _sequence = startSequence;
    }

    /// <summary>已产生的帧数。</summary>
    public ulong FramesEmitted => _framesEmitted;

    /// <summary>当前未凑满一帧的字节数。</summary>
    public int PendingBytes => _pendingBytes;

    /// <summary>追加一段 PCM16LE 字节，返回本次凑满的帧。</summary>
    /// <param name="pcm">16kHz 单声道 PCM16LE。</param>
    /// <returns>可发送的帧，按时间顺序。</returns>
    public IReadOnlyList<AudioFrame> Append(ReadOnlySpan<byte> pcm)
    {
        if (pcm.IsEmpty)
        {
            return [];
        }

        var frames = new List<AudioFrame>(pcm.Length / AudioFrameCodec.PayloadBytes + 1);
        var offset = 0;

        while (offset < pcm.Length)
        {
            var needed = AudioFrameCodec.PayloadBytes - _pendingBytes;
            var available = pcm.Length - offset;
            var take = Math.Min(needed, available);

            pcm.Slice(offset, take).CopyTo(_pending.AsSpan(_pendingBytes));
            _pendingBytes += take;
            offset += take;

            if (_pendingBytes < AudioFrameCodec.PayloadBytes)
            {
                break;
            }

            frames.Add(BuildFrame());
            _pendingBytes = 0;
        }

        return frames;
    }

    private AudioFrame BuildFrame()
    {
        var timestamp = _startTimestampNs + (_framesEmitted * (ulong)(AudioFrameCodec.FrameDurationMs * 1_000_000));
        var frame = new AudioFrame(
            AudioFrameCodec.ProtocolVersion,
            _source,
            AudioFrameCodec.FrameDurationMs,
            _sequence,
            timestamp,
            AudioFrameCodec.SampleRate,
            AudioFrameCodec.Channels,
            AudioFrameCodec.SampleFormatPcm16,
            _pending.ToArray());

        _framesEmitted++;
        _sequence = unchecked(_sequence + 1);
        return frame;
    }
}
