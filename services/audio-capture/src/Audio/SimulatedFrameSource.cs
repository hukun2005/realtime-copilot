using System.Diagnostics;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 无音频设备的模拟帧源：以 50fps 产出 20ms 静音帧，用于在 CI/无端点环境验证控制链路。
/// </summary>
/// <remarks>
/// 只在 <c>RTC_CAPTURE_SIMULATED=1</c> 时使用；它不打开任何设备，也不进入产品默认路径。
/// </remarks>
public sealed class SimulatedFrameSource : IAudioFrameSource
{
    private readonly byte[] _payload = new byte[AudioFrameCodec.PayloadBytes];
    private readonly TaskCompletionSource _started = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private CancellationTokenSource? _cancellation;
    private Task? _loop;
    private AudioFramer? _framer;

    /// <inheritdoc />
    public string Source => "loopback";

    /// <inheritdoc />
    public event EventHandler<AudioFrame>? FrameAvailable;

    /// <inheritdoc />
    public event EventHandler<CaptureFailure>? Failed;

    /// <summary>帧源已开始产出。</summary>
    public Task Started => _started.Task;

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        _framer = new AudioFramer(AudioFrameCodec.SourceLoopback, MonotonicClock.Nanoseconds());
        _cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _loop = Task.Run(() => PumpAsync(_cancellation.Token), CancellationToken.None);
        _started.TrySetResult();
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public async Task StopAsync(CancellationToken cancellationToken)
    {
        _cancellation?.Cancel();
        if (_loop is not null)
        {
            try
            {
                await _loop.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // 正常停止。
            }
        }

        _cancellation?.Dispose();
        _cancellation = null;
        _loop = null;
        _framer = null;
        GC.KeepAlive(cancellationToken);
    }

    /// <inheritdoc />
    public async ValueTask DisposeAsync()
    {
        await StopAsync(CancellationToken.None).ConfigureAwait(false);
        GC.SuppressFinalize(this);
    }

    private async Task PumpAsync(CancellationToken cancellationToken)
    {
        var clock = Stopwatch.GetTimestamp();
        var tick = (long)(AudioFrameCodec.FrameDurationMs / 1000.0 * Stopwatch.Frequency);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var framer = _framer;
                if (framer is null)
                {
                    return;
                }

                foreach (var frame in framer.Append(_payload))
                {
                    FrameAvailable?.Invoke(this, frame);
                }

                var target = clock + ((long)framer.FramesEmitted * tick);
                var now = Stopwatch.GetTimestamp();
                if (now < target)
                {
                    await Task.Delay(TimeSpan.FromSeconds((double)(target - now) / Stopwatch.Frequency), cancellationToken)
                        .ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // 正常停止。
        }
        catch (Exception exception)
        {
            Failed?.Invoke(this, new CaptureFailure("capture_failed", exception.Message));
        }
    }
}
