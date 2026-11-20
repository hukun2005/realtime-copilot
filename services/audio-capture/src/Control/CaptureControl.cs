using System.Runtime.Versioning;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using RealtimeCopilot.AudioCapture.Audio;
using RealtimeCopilot.AudioCapture.Capture;

namespace RealtimeCopilot.AudioCapture.Control;

/// <summary>一条 <c>audio.status</c> 上报。</summary>
/// <param name="State">idle/starting/capturing/stopping/error。</param>
/// <param name="Source">loopback/mic/null。</param>
/// <param name="DeviceId">设备端点 ID。</param>
/// <param name="Mode">loopback/mic/process。</param>
/// <param name="Since">进入该状态的时间戳（毫秒）。</param>
/// <param name="Error">人类可读错误说明。</param>
/// <param name="ErrorCode">稳定错误码。</param>
public sealed record AudioStatusReport(
    [property: System.Text.Json.Serialization.JsonPropertyName("state")] string State,
    [property: System.Text.Json.Serialization.JsonPropertyName("source")] string? Source,
    [property: System.Text.Json.Serialization.JsonPropertyName("deviceId")] string? DeviceId,
    [property: System.Text.Json.Serialization.JsonPropertyName("mode")] string? Mode,
    [property: System.Text.Json.Serialization.JsonPropertyName("since")] long Since,
    [property: System.Text.Json.Serialization.JsonPropertyName("error")] string? Error = null,
    [property: System.Text.Json.Serialization.JsonPropertyName("errorCode")] string? ErrorCode = null);

/// <summary>
/// 控制模式下的采集执行器：响应 capture.start / capture.stop，并上报 audio.status。
/// </summary>
/// <remarks>
/// 由 Core 拉起时使用（<c>RTC_CAPTURE_CONTROL=1</c>）。没有收到 start 就不会采集；
/// 设备变化、采集失败都会进入 error 并停止，不做静默重启。
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class CaptureControl : IAsyncDisposable
{
    private readonly CoreClient _client;
    private readonly ILoggerFactory _loggers;
    private readonly ILogger _logger;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly List<Session> _sessions = [];
    private CancellationTokenSource? _deviceWatch;
    private bool _disposed;

    private sealed record Session(
        IAudioFrameSource Source,
        FrameStreamer Streamer,
        CancellationTokenSource Cancellation,
        Task Task);

    /// <summary>创建执行器。</summary>
    /// <param name="client">已连接的 Core 客户端。</param>
    /// <param name="loggers">日志工厂。</param>
    /// <param name="logger">控制面日志。</param>
    public CaptureControl(CoreClient client, ILoggerFactory loggers, ILogger logger)
    {
        _client = client;
        _loggers = loggers;
        _logger = logger;
    }

    /// <summary>当前是否在采集。</summary>
    public bool Capturing => _sessions.Count > 0;

    /// <summary>处理控制命令。</summary>
    /// <param name="type">capture.start 或 capture.stop。</param>
    /// <param name="payload">命令负载。</param>
    /// <returns>处理完成的任务。</returns>
    public async Task HandleAsync(string type, JsonElement payload)
    {
        if (type == "capture.start")
        {
            await StartAsync(payload).ConfigureAwait(false);
        }
        else
        {
            await StopAsync(CancellationToken.None).ConfigureAwait(false);
        }
    }

    private async Task StartAsync(JsonElement payload)
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_sessions.Count > 0)
            {
                // 幂等：已经在采集就不再拉起第二份。
                _logger.LogInformation("capture.start ignored: already capturing");
                await PublishAsync("capturing", null, null, null, null, CancellationToken.None).ConfigureAwait(false);
                return;
            }

            var deviceId = ReadOptionalString(payload, "loopback", "deviceId");
            var micEnabled = payload.TryGetProperty("mic", out var mic)
                && mic.TryGetProperty("enabled", out var enabled)
                && enabled.ValueKind == JsonValueKind.True;

            await PublishAsync("starting", "loopback", deviceId, "loopback", null, CancellationToken.None).ConfigureAwait(false);

            try
            {
                if (string.Equals(Environment.GetEnvironmentVariable("RTC_CAPTURE_SIMULATED"), "1", StringComparison.Ordinal))
                {
                    // 无音频端点的 CI：用静音帧源验证控制链路，不打开设备。
                    StartSource(new SimulatedFrameSource(), "loopback");
                }
                else
                {
                    StartSource(new LoopbackCaptureSource(
                        DeviceCatalog.Resolve(deviceId, NAudio.CoreAudioApi.DataFlow.Render),
                        _loggers.CreateLogger<LoopbackCaptureSource>()), "loopback");
                }

                if (micEnabled)
                {
                    StartSource(new MicrophoneCaptureSource(
                        null,
                        _loggers.CreateLogger<MicrophoneCaptureSource>()), "mic");
                }

                await Task.WhenAll(_sessions.Select(session => session.Streamer.Started)).WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
            }
            catch (Exception exception) when (exception is not OperationCanceledException)
            {
                _logger.LogError(exception, "capture start failed");
                await StopSessionsAsync().ConfigureAwait(false);
                await PublishAsync("error", "loopback", deviceId, "loopback", exception.Message, "capture_failed", CancellationToken.None).ConfigureAwait(false);
                return;
            }

            StartDeviceWatch(deviceId);
            _logger.LogInformation("capture started: sources={Count}", _sessions.Count);
            await PublishAsync("capturing", "loopback", deviceId, "loopback", null, CancellationToken.None).ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>停止采集并上报 idle。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>停止完成的任务。</returns>
    public async Task StopAsync(CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            _deviceWatch?.Cancel();
            _deviceWatch = null;
            if (_sessions.Count == 0)
            {
                await PublishAsync("idle", null, null, null, null, cancellationToken).ConfigureAwait(false);
                return;
            }

            await PublishAsync("stopping", null, null, null, null, cancellationToken).ConfigureAwait(false);
            await StopSessionsAsync().ConfigureAwait(false);
            _logger.LogInformation("capture stopped");
            await PublishAsync("idle", null, null, null, null, cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <inheritdoc />
    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _deviceWatch?.Cancel();
        await StopSessionsAsync().ConfigureAwait(false);
        _gate.Dispose();
        GC.SuppressFinalize(this);
    }

    private void StartSource(IAudioFrameSource source, string mode)
    {
        var cancellation = new CancellationTokenSource();
        var streamer = new FrameStreamer(
            source,
            _client.SendBinaryAsync,
            _loggers.CreateLogger<FrameStreamer>());
        var task = streamer.RunAsync(cancellation.Token);
        var session = new Session(source, streamer, cancellation, task);
        _sessions.Add(session);

        _ = MonitorAsync(session, mode);
    }

    private async Task MonitorAsync(Session session, string mode)
    {
        try
        {
            await session.Task.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }
        catch (Exception exception)
        {
            _logger.LogError(exception, "capture failed: {Message}", exception.Message);
            var code = exception is CaptureFailedException failed ? failed.Code : "capture_failed";
            await PublishAsync("error", mode, null, mode, exception.Message, code, CancellationToken.None).ConfigureAwait(false);
            _deviceWatch?.Cancel();
        }
    }

    private async Task StopSessionsAsync()
    {
        var sessions = _sessions.ToList();
        _sessions.Clear();
        foreach (var session in sessions)
        {
            session.Cancellation.Cancel();
        }

        foreach (var session in sessions)
        {
            try
            {
                await session.Task.WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // 已经记录过失败原因；停止流程继续。
            }

            session.Cancellation.Dispose();
        }
    }

    private void StartDeviceWatch(string? deviceId)
    {
        _deviceWatch?.Cancel();
        if (deviceId is not null)
        {
            // 指定端点时不做默认设备轮询。
            return;
        }

        var baseline = DeviceCatalog.DefaultId(NAudio.CoreAudioApi.DataFlow.Render);
        var cancellation = new CancellationTokenSource();
        _deviceWatch = cancellation;
        _ = WatchDeviceAsync(baseline, cancellation.Token);
    }

    private async Task WatchDeviceAsync(string? baseline, CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken).ConfigureAwait(false);
                var current = DeviceCatalog.DefaultId(NAudio.CoreAudioApi.DataFlow.Render);
                if (!string.Equals(current, baseline, StringComparison.Ordinal))
                {
                    _logger.LogWarning("default audio device changed during capture");
                    await PublishAsync("error", "loopback", current, "loopback", "the default output device changed", "device_changed", CancellationToken.None).ConfigureAwait(false);
                    await StopAsync(CancellationToken.None).ConfigureAwait(false);
                    return;
                }
            }
        }
        catch (OperationCanceledException)
        {
            // 正常停止。
        }
    }

    private Task PublishAsync(
        string state,
        string? source,
        string? deviceId,
        string? mode,
        string? error,
        CancellationToken cancellationToken) =>
        PublishAsync(state, source, deviceId, mode, error, null, cancellationToken);

    private async Task PublishAsync(
        string state,
        string? source,
        string? deviceId,
        string? mode,
        string? error,
        string? errorCode,
        CancellationToken cancellationToken = default)
    {
        var report = new AudioStatusReport(
            state,
            source,
            deviceId,
            mode,
            DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            error,
            errorCode);
        try
        {
            await _client.SendStatusAsync(report, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is CoreConnectionException or OperationCanceledException)
        {
            _logger.LogDebug("status report dropped: {Message}", exception.Message);
        }
    }

    private static string? ReadOptionalString(JsonElement payload, string section, string property)
    {
        if (!payload.TryGetProperty(section, out var element) || element.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }
}
