using System.Runtime.Versioning;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 把 <see cref="IAudioFrameSource"/> 产生的帧按到达顺序发送给 Core。
/// </summary>
/// <remarks>
/// 采集回调在设备线程上，发送是异步 I/O，因此中间用有界 channel 解耦：
/// 队列满时丢弃最旧的帧（实时音频宁可丢帧也不阻塞设备回调）。
/// </remarks>
public sealed class FrameStreamer
{
    private readonly IAudioFrameSource _source;
    private readonly Func<ReadOnlyMemory<byte>, CancellationToken, Task> _send;
    private readonly ILogger _logger;
    private readonly TaskCompletionSource _started = new(TaskCreationOptions.RunContinuationsAsynchronously);

    /// <summary>创建发送器。</summary>
    /// <param name="source">音频来源。</param>
    /// <param name="send">发送一帧字节的委托。</param>
    /// <param name="logger">日志。</param>
    public FrameStreamer(
        IAudioFrameSource source,
        Func<ReadOnlyMemory<byte>, CancellationToken, Task> send,
        ILogger logger)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(send);
        ArgumentNullException.ThrowIfNull(logger);

        _source = source;
        _send = send;
        _logger = logger;
    }

    /// <summary>采集源已启动（或启动失败）时完成。</summary>
    public Task Started => _started.Task;

    /// <summary>已发送帧数。</summary>
    public uint FramesSent { get; private set; }

    /// <summary>因队列已满而丢弃的帧数。</summary>
    public uint FramesDropped { get; private set; }

    /// <summary>启动采集并持续发送，直到取消或采集失败。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>取消或失败时完成的任务。</returns>
    /// <exception cref="CoreConnectionException">发送失败。</exception>
    /// <exception cref="CaptureFailedException">采集端报告失败。</exception>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var channel = Channel.CreateBounded<AudioFrame>(new BoundedChannelOptions(200)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
            SingleWriter = false,
        });

        var failure = new TaskCompletionSource<CaptureFailure>(TaskCreationOptions.RunContinuationsAsynchronously);

        void OnFrame(object? sender, AudioFrame frame)
        {
            if (!channel.Writer.TryWrite(frame))
            {
                FramesDropped++;
            }
        }

        void OnFailed(object? sender, CaptureFailure error) => failure.TrySetResult(error);

        _source.FrameAvailable += OnFrame;
        _source.Failed += OnFailed;

        try
        {
            await _source.StartAsync(cancellationToken).ConfigureAwait(false);
            _started.TrySetResult();
            _logger.LogInformation("capture started: source={Source}", _source.Source);

            while (!cancellationToken.IsCancellationRequested)
            {
                var readTask = channel.Reader.ReadAsync(cancellationToken).AsTask();
                var completed = await Task.WhenAny(readTask, failure.Task).ConfigureAwait(false);

                if (completed == failure.Task)
                {
                    var error = await failure.Task.ConfigureAwait(false);
                    throw new CaptureFailedException(error.Code, error.Message);
                }

                var frame = await readTask.ConfigureAwait(false);
                await _send(AudioFrameCodec.Serialize(frame), cancellationToken).ConfigureAwait(false);
                FramesSent++;
                if (FramesSent == 1)
                {
                    _logger.LogInformation(
                        "first captured frame sent: sequence={Sequence} timestampNs={Timestamp}",
                        frame.Sequence,
                        frame.CaptureTimestampNs);
                }
            }
        }
        catch (OperationCanceledException)
        {
            _started.TrySetCanceled(cancellationToken);
            // 正常停止。
        }
        finally
        {
            _source.FrameAvailable -= OnFrame;
            _source.Failed -= OnFailed;
            await _source.StopAsync(CancellationToken.None).ConfigureAwait(false);
            _logger.LogInformation(
                "capture stopped: source={Source} sent={Sent} dropped={Dropped}",
                _source.Source,
                FramesSent,
                FramesDropped);
        }
    }
}

/// <summary>采集端失败：设备丢失或驱动停止。</summary>
public sealed class CaptureFailedException : Exception
{
    /// <summary>创建异常。</summary>
    /// <param name="code">稳定错误码。</param>
    /// <param name="message">诊断说明。</param>
    public CaptureFailedException(string code, string message)
        : base(message)
    {
        Code = code;
    }

    /// <summary>稳定错误码。</summary>
    public string Code { get; }
}
