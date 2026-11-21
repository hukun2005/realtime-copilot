using System.Runtime.Versioning;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 把 <see cref="IAudioFrameSource"/> 产生的帧按到达顺序发送给 Core。
/// </summary>
/// <remarks>
/// 采集回调在设备线程上，发送是异步 I/O，因此中间用有界 channel 解耦：
/// 队列满时丢弃**新到**的帧并计数（实时音频宁可丢帧也不阻塞设备回调）。
/// 使用 Wait 模式让 <c>TryWrite</c> 在满载时返回 false：不阻塞回调，同时让丢弃可观测。
///
/// 生命周期：<see cref="RunAsync"/> 负责启动与消费；<see cref="StopAsync"/> 与
/// <see cref="DisposeAsync"/> 幂等，统一取消令牌、完成 writer、等待消费者并停止采集源。
/// </remarks>
public sealed class FrameStreamer : IAsyncDisposable
{
    private const int ChannelCapacity = 200;

    private readonly IAudioFrameSource _source;
    private readonly Func<ReadOnlyMemory<byte>, CancellationToken, Task> _send;
    private readonly ILogger _logger;
    private readonly TaskCompletionSource _started = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly SemaphoreSlim _lifecycle = new(1, 1);

    private CancellationTokenSource? _cts;
    private Channel<AudioFrame>? _channel;
    private Task? _consumer;
    private EventHandler<AudioFrame>? _onFrame;
    private EventHandler<CaptureFailure>? _onFailed;
    private int _dropped;
    private bool _stopped;
    private bool _disposed;

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

    /// <summary>因队列已满而丢弃的帧数（可从任意线程安全读取）。</summary>
    public uint FramesDropped => (uint)Volatile.Read(ref _dropped);

    /// <summary>启动采集并持续发送，直到取消、采集失败或发送失败。</summary>
    /// <param name="cancellationToken">外部取消令牌。</param>
    /// <returns>取消或失败时完成的任务。</returns>
    /// <exception cref="CoreConnectionException">发送失败。</exception>
    /// <exception cref="CaptureFailedException">采集端报告失败。</exception>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        _cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var token = _cts.Token;
        _channel = Channel.CreateBounded<AudioFrame>(new BoundedChannelOptions(ChannelCapacity)
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = false,
        });

        var failure = new TaskCompletionSource<CaptureFailure>(TaskCreationOptions.RunContinuationsAsynchronously);

        _onFrame = (_, frame) =>
        {
            if (_channel is { } channel && channel.Writer.TryWrite(frame))
            {
                return;
            }

            Interlocked.Increment(ref _dropped);
        };
        _onFailed = (_, error) => failure.TrySetResult(error);

        _source.FrameAvailable += _onFrame;
        _source.Failed += _onFailed;

        try
        {
            await _source.StartAsync(token).ConfigureAwait(false);
            _started.TrySetResult();
            _logger.LogInformation("capture started: source={Source}", _source.Source);

            _consumer = ConsumeAsync(_channel.Reader, token);
            var completed = await Task.WhenAny(_consumer, failure.Task).ConfigureAwait(false);

            if (completed == failure.Task)
            {
                var error = await failure.Task.ConfigureAwait(false);
                throw new CaptureFailedException(error.Code, error.Message);
            }

            // 消费者异常（发送失败）在这里向上抛；正常取消则不抛。
            await _consumer.ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            _started.TrySetCanceled(token);
        }
        finally
        {
            await StopAsync().ConfigureAwait(false);
        }
    }

    /// <summary>停止采集与发送；可重复调用。</summary>
    /// <returns>清理完成的任务。</returns>
    public async Task StopAsync()
    {
        await _lifecycle.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_stopped)
            {
                return;
            }

            _stopped = true;

            if (_onFrame is not null)
            {
                _source.FrameAvailable -= _onFrame;
            }

            if (_onFailed is not null)
            {
                _source.Failed -= _onFailed;
            }

            _cts?.Cancel();
            _channel?.Writer.TryComplete();

            try
            {
                await _source.StopAsync(CancellationToken.None).ConfigureAwait(false);
            }
            catch (Exception exception)
            {
                // 采集源停止失败不应阻止其余清理，但要可见。
                _logger.LogWarning(exception, "capture source stop failed: source={Source}", _source.Source);
            }

            if (_consumer is not null)
            {
                try
                {
                    await _consumer.ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    // 正常停止。
                }
                catch (Exception exception)
                {
                    // 发送失败已在 RunAsync 中抛出；这里只观察，避免未观察异常。
                    _logger.LogDebug(exception, "consumer finished with error");
                }
            }

            _logger.LogInformation(
                "capture stopped: source={Source} sent={Sent} dropped={Dropped}",
                _source.Source,
                FramesSent,
                FramesDropped);
        }
        finally
        {
            _lifecycle.Release();
        }
    }

    /// <summary>释放资源；可重复调用。</summary>
    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }

        await StopAsync().ConfigureAwait(false);
        _disposed = true;
        _cts?.Dispose();
        _cts = null;
        _lifecycle.Dispose();
    }

    private async Task ConsumeAsync(ChannelReader<AudioFrame> reader, CancellationToken token)
    {
        while (await reader.WaitToReadAsync(token).ConfigureAwait(false))
        {
            while (reader.TryRead(out var frame))
            {
                await _send(AudioFrameCodec.Serialize(frame), token).ConfigureAwait(false);
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
