using System.Diagnostics;
using Microsoft.Extensions.Logging;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 按固定帧率把模拟音频推给发送端，并可选地制造序号空洞。
/// </summary>
/// <remarks>
/// 发送动作通过委托注入：生产环境传 <c>CoreClient.SendBinaryAsync</c>，
/// 测试传内存收集器，二者走同一套节拍与序号逻辑。
/// </remarks>
public sealed class AudioStreamer
{
    private readonly Func<ReadOnlyMemory<byte>, CancellationToken, Task> _send;
    private readonly SimulatedAudioSource _source;
    private readonly ILogger _logger;
    private readonly int _framesPerSecond;
    private readonly int _dropEvery;
    private readonly Func<ulong> _timestamp;

    /// <summary>创建发送器。</summary>
    /// <param name="send">发送一帧字节的委托。</param>
    /// <param name="source">帧来源。</param>
    /// <param name="logger">日志。</param>
    /// <param name="framesPerSecond">帧率；必须为正。</param>
    /// <param name="dropEvery">每 N 帧跳过一次；0 表示不丢帧。</param>
    /// <param name="timestamp">时间戳来源，默认使用单调时钟。</param>
    public AudioStreamer(
        Func<ReadOnlyMemory<byte>, CancellationToken, Task> send,
        SimulatedAudioSource source,
        ILogger logger,
        int framesPerSecond = 50,
        int dropEvery = 0,
        Func<ulong>? timestamp = null)
    {
        ArgumentNullException.ThrowIfNull(send);
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(logger);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(framesPerSecond);

        _send = send;
        _source = source;
        _logger = logger;
        _framesPerSecond = framesPerSecond;
        _dropEvery = dropEvery;
        _timestamp = timestamp ?? MonotonicClock.Nanoseconds;
    }

    /// <summary>已发送帧数。</summary>
    public uint FramesSent { get; private set; }

    /// <summary>主动跳过的帧数（序号仍然递增，制造可检测的空洞）。</summary>
    public uint FramesSkipped { get; private set; }

    /// <summary>按帧率持续发送，直到取消。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>取消时完成的任务。</returns>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var interval = TimeSpan.FromSeconds(1.0 / _framesPerSecond);
        var deadline = Stopwatch.GetTimestamp();
        var generated = 0L;

        while (!cancellationToken.IsCancellationRequested)
        {
            generated++;
            var frame = _source.Next(_timestamp());
            var shouldDrop = _dropEvery > 0 && generated % _dropEvery == 0;

            if (shouldDrop)
            {
                FramesSkipped++;
            }
            else
            {
                var bytes = AudioFrameCodec.Serialize(frame);
                await _send(bytes, cancellationToken).ConfigureAwait(false);
                FramesSent++;
                if (FramesSent == 1)
                {
                    _logger.LogInformation(
                        "first audio frame sent: sequence={Sequence} bytes={Bytes}",
                        frame.Sequence,
                        bytes.Length);
                }
            }

            var nextDeadline = deadline + (long)(interval.TotalSeconds * Stopwatch.Frequency);
            var now = Stopwatch.GetTimestamp();
            if (now < nextDeadline)
            {
                var delay = TimeSpan.FromSeconds((double)(nextDeadline - now) / Stopwatch.Frequency);
                try
                {
                    await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }

                deadline = nextDeadline;
            }
            else
            {
                // 落后时不补发，重置节拍避免追帧造成突发流量。
                deadline = now;
            }
        }
    }

    /// <summary>输出一行可被脚本解析的汇总。</summary>
    /// <returns>JSON 文本。</returns>
    public string ToJson() => $"{{\"sent\":{FramesSent},\"skipped\":{FramesSkipped}}}";
}
