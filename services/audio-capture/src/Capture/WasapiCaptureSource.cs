using System.Diagnostics;
using System.Runtime.Versioning;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using NAudio.Wave.SampleProviders;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Capture;

/// <summary>
/// WASAPI 采集基类：设备回调 → 缓冲 → 重采样到 16kHz 单声道 → 20ms 分帧 → 事件。
/// </summary>
/// <remarks>
/// 使用 NAudio 3.x 的 <see cref="WasapiRecorderBuilder"/>（<c>WasapiCapture</c> 已标记过时）。
/// 只在 Windows 上实例化；Linux CI 只编译并运行纯逻辑测试。
/// 设备线程只做入缓冲，重采样与分帧在独立泵任务上执行，避免阻塞音频回调。
/// </remarks>
[SupportedOSPlatform("windows")]
public abstract class WasapiCaptureSourceBase : IAudioFrameSource
{
    private readonly MMDevice? _device;
    private readonly ILogger _logger;
    private WasapiRecorder? _recorder;
    private BufferedWaveProvider? _buffer;
    private CancellationTokenSource? _cancellation;
    private Task? _pump;
    private AudioFramer? _framer;
    private CaptureDataAvailableHandler? _dataHandler;
    private EventHandler<StoppedEventArgs>? _stoppedHandler;
    private long _bufferOverflows;
    private long _callbacks;
    private volatile bool _stopping;

    /// <summary>创建采集源。</summary>
    /// <param name="device">目标端点；<c>null</c> 表示系统默认设备。</param>
    /// <param name="logger">诊断日志。</param>
    protected WasapiCaptureSourceBase(MMDevice? device, ILogger? logger = null)
    {
        _device = device;
        _logger = logger ?? NullLogger.Instance;
    }

    /// <inheritdoc />
    public abstract string Source { get; }

    /// <inheritdoc />
    public event EventHandler<AudioFrame>? FrameAvailable;

    /// <inheritdoc />
    public event EventHandler<CaptureFailure>? Failed;

    /// <summary>缓冲区溢出次数（设备回调快于泵任务时发生）。</summary>
    public long BufferOverflows => Interlocked.Read(ref _bufferOverflows);

    /// <summary>设备回调次数。</summary>
    public long Callbacks => Interlocked.Read(ref _callbacks);

    /// <summary>是否按 loopback 方式打开（否则按采集设备打开）。</summary>
    protected abstract bool IsLoopback { get; }

    /// <summary>协议来源码。</summary>
    protected abstract byte SourceCode { get; }

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        if (_recorder is not null)
        {
            return Task.CompletedTask;
        }

        // 只有显式指定端点时才 WithDevice：本机实测用 WithDevice(默认端点) 打开 loopback
        // 时，其他进程播放的音频会被采成静音；交给 builder 解析默认端点则正常。
        var builder = new WasapiRecorderBuilder()
            .WithSharedMode()
            .WithBufferLength(200);
        if (_device is not null)
        {
            builder.WithDevice(_device);
        }

        if (IsLoopback)
        {
            builder.WithLoopbackCapture();
        }

        var recorder = builder.Build();
        _recorder = recorder;
        // 设备通常给出 WAVE_FORMAT_EXTENSIBLE；转成标准格式后 ToSampleProvider 才能正确识别 float/PCM。
        var captureFormat = recorder.WaveFormat.AsStandardWaveFormat();
        _buffer = new BufferedWaveProvider(captureFormat, TimeSpan.FromSeconds(2))
        {
            DiscardOnBufferOverflow = true,
            // 数据不足时只返回已有样本，不能补零：补零会把真实音频静音化。
            ReadFully = false,
        };
        _framer = new AudioFramer(SourceCode, MonotonicClock.Nanoseconds());
        _cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);

        _dataHandler = OnDataAvailable;
        _stoppedHandler = OnRecordingStopped;
        recorder.DataAvailable += _dataHandler;
        recorder.RecordingStopped += _stoppedHandler;
        recorder.StartRecording();

        _pump = Task.Run(() => PumpAsync(_cancellation.Token), CancellationToken.None);
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public async Task StopAsync(CancellationToken cancellationToken)
    {
        _stopping = true;
        _cancellation?.Cancel();

        var recorder = _recorder;
        if (recorder is not null)
        {
            try
            {
                recorder.StopRecording();
            }
            catch (Exception)
            {
                // 设备已消失时 StopRecording 可能抛异常；停止流程必须继续。
            }
        }

        if (_pump is not null)
        {
            try
            {
                await _pump.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // 正常取消。
            }
        }

        if (recorder is not null)
        {
            if (_dataHandler is not null)
            {
                recorder.DataAvailable -= _dataHandler;
            }

            if (_stoppedHandler is not null)
            {
                recorder.RecordingStopped -= _stoppedHandler;
            }

            await recorder.DisposeAsync().ConfigureAwait(false);
        }

        _pump = null;
        _recorder = null;
        _buffer = null;
        _framer = null;
        _dataHandler = null;
        _stoppedHandler = null;
        _cancellation?.Dispose();
        _cancellation = null;
        _stopping = false;
        GC.KeepAlive(cancellationToken);
    }

    /// <inheritdoc />
    public async ValueTask DisposeAsync()
    {
        await StopAsync(CancellationToken.None).ConfigureAwait(false);
        GC.SuppressFinalize(this);
    }


    private void OnDataAvailable(
        ReadOnlySpan<byte> data,
        AudioClientBufferFlags flags,
        long devicePosition,
        long qpcPosition)
    {
        var buffer = _buffer;
        if (buffer is null || data.IsEmpty)
        {
            return;
        }

        if (Interlocked.Increment(ref _callbacks) == 1)
        {
            _logger.LogInformation(
                "first capture buffer: bytes={Bytes} format={Format} device={Device}",
                data.Length,
                buffer.WaveFormat,
                _device?.FriendlyName ?? "<default>");
        }

        buffer.AddSamples(data);
        if (buffer.BufferedBytes + data.Length > buffer.BufferLength)
        {
            Interlocked.Increment(ref _bufferOverflows);
        }
    }

    private void OnRecordingStopped(object? sender, StoppedEventArgs args)
    {
        if (_stopping)
        {
            return;
        }

        var message = args.Exception?.Message ?? "the capture device stopped unexpectedly";
        Failed?.Invoke(this, new CaptureFailure("device_lost", message));
    }

    private async Task PumpAsync(CancellationToken cancellationToken)
    {
        try
        {
            var pipeline = BuildPipeline();
            var samples = new float[AudioFrameCodec.PayloadBytes / 2];
            var bytes = new byte[AudioFrameCodec.PayloadBytes];
            var clock = Stopwatch.GetTimestamp();
            var frameTicks = (long)(AudioFrameCodec.FrameDurationMs / 1000.0 * Stopwatch.Frequency);

            while (!cancellationToken.IsCancellationRequested)
            {
                var framer = _framer;
                if (framer is null)
                {
                    return;
                }

                // 按"已产出的帧数 × 20ms"限速：缓冲区里可能堆了数秒数据，
                // 不限速就会瞬间把积压灌给 Core（触发 rate_limited）。
                var target = clock + ((long)framer.FramesEmitted * frameTicks);
                var now = Stopwatch.GetTimestamp();
                if (now < target)
                {
                    var delay = TimeSpan.FromSeconds((double)(target - now) / Stopwatch.Frequency);
                    await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                    continue;
                }

                // 落后超过 2 秒（例如系统挂起后恢复）就丢弃积压，避免长时间追赶。
                if (now - target > Stopwatch.Frequency * 2)
                {
                    _buffer?.ClearBuffer();
                }

                var read = pipeline.Read(samples);
                if (read <= 0)
                {
                    await Task.Delay(5, cancellationToken).ConfigureAwait(false);
                    continue;
                }


                var written = Pcm16.FromFloat(samples.AsSpan(0, read), bytes);
                foreach (var frame in framer.Append(bytes.AsSpan(0, written)))
                {
                    FrameAvailable?.Invoke(this, frame);
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

    private ISampleProvider BuildPipeline()
    {
        var buffer = _buffer ?? throw new InvalidOperationException("capture is not started");
        ISampleProvider samples = buffer.ToSampleProvider();

        var channels = samples.WaveFormat.Channels;
        if (channels == 2)
        {
            samples = new StereoToMonoSampleProvider(samples) { LeftVolume = 0.5f, RightVolume = 0.5f };
        }
        else if (channels > 2)
        {
            throw new NotSupportedException($"multi-channel capture ({channels} channels) is not supported yet");
        }

        if (samples.WaveFormat.SampleRate != (int)AudioFrameCodec.SampleRate)
        {
            samples = new WdlResamplingSampleProvider(samples, (int)AudioFrameCodec.SampleRate);
        }

        return samples;
    }
}

/// <summary>系统输出（loopback）采集。</summary>
[SupportedOSPlatform("windows")]
public sealed class LoopbackCaptureSource : WasapiCaptureSourceBase
{
    /// <summary>创建 loopback 采集源。</summary>
    /// <param name="device">渲染端点；<c>null</c> 表示默认输出设备。</param>
    /// <param name="logger">诊断日志。</param>
    public LoopbackCaptureSource(MMDevice? device = null, ILogger? logger = null)
        : base(device, logger)
    {
    }

    /// <inheritdoc />
    public override string Source => "loopback";

    /// <inheritdoc />
    protected override bool IsLoopback => true;

    /// <inheritdoc />
    protected override byte SourceCode => AudioFrameCodec.SourceLoopback;
}

/// <summary>麦克风采集（默认关闭，需显式开启）。</summary>
[SupportedOSPlatform("windows")]
public sealed class MicrophoneCaptureSource : WasapiCaptureSourceBase
{
    /// <summary>创建麦克风采集源。</summary>
    /// <param name="device">采集端点；<c>null</c> 表示默认输入设备。</param>
    /// <param name="logger">诊断日志。</param>
    public MicrophoneCaptureSource(MMDevice? device = null, ILogger? logger = null)
        : base(device, logger)
    {
    }

    /// <inheritdoc />
    public override string Source => "mic";

    /// <inheritdoc />
    protected override bool IsLoopback => false;

    /// <inheritdoc />
    protected override byte SourceCode => AudioFrameCodec.SourceMic;
}
