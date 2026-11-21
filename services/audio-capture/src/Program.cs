using System.Text.Json;
using Microsoft.Extensions.Logging;
using RealtimeCopilot.AudioCapture.Audio;
using RealtimeCopilot.AudioCapture.Control;

namespace RealtimeCopilot.AudioCapture;

/// <summary>进程入口。</summary>
/// <remarks>
/// 退出码：0 正常退出；2 配置非法或平台不支持；3 无法连接 Core；4 连接异常、发送失败或采集中断。
/// </remarks>
internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        if (args.Contains("--version"))
        {
            var version = typeof(Program).Assembly.GetName().Version?.ToString(3) ?? "unknown";
            Console.Out.WriteLine(version);
            return 0;
        }

        if (args.Contains("--list-devices"))
        {
            var devices = Capture.DeviceCatalog.List();
            Console.Out.WriteLine(JsonSerializer.Serialize(devices, new JsonSerializerOptions
            {
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            }));
            return 0;
        }

        AppConfig config;
        SimulationOptions simulation;
        CaptureOptions capture;
        try
        {
            config = AppConfig.Load(args, Environment.GetEnvironmentVariables());
            simulation = SimulationOptions.Parse(args);
            capture = CaptureOptions.Parse(args);
        }
        catch (ConfigurationException exception)
        {
            Console.Error.WriteLine($"configuration error: {exception.Message}");
            return 2;
        }

        using var loggerFactory = LoggingSetup.Create(config.LogLevel);
        var logger = loggerFactory.CreateLogger("audio-capture");
        logger.LogInformation(
            "starting audio-capture: role={Role} core={Core} runtime={Runtime}",
            config.Role,
            config.CoreWsUrl,
            config.RuntimePath ?? "<none>");

        using var shutdown = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            shutdown.Cancel();
        };

        await using var client = new CoreClient(config, loggerFactory.CreateLogger<CoreClient>());
        try
        {
            await client.ConnectAsync(shutdown.Token).ConfigureAwait(false);
        }
        catch (CoreConnectionException exception)
        {
            logger.LogError("connect failed: {Code}: {Message}", exception.Code, exception.Message);
            return 3;
        }
        catch (OperationCanceledException)
        {
            return 0;
        }

        if (config.Control)
        {
            if (!OperatingSystem.IsWindows())
            {
                logger.LogError("control mode requires Windows");
                return 2;
            }

            return await RunControlAsync(client, loggerFactory, logger, shutdown.Token).ConfigureAwait(false);
        }

        if (simulation.Enabled)
        {
            return await RunSimulationAsync(client, simulation, loggerFactory, logger, shutdown.Token).ConfigureAwait(false);
        }

        if (capture.Enabled)
        {
            return await RunCaptureAsync(client, capture, simulation, loggerFactory, logger, shutdown.Token).ConfigureAwait(false);
        }

        var reason = await client.RunAsync(shutdown.Token).ConfigureAwait(false);
        logger.LogInformation("stopped: {Reason}", reason);
        return reason switch
        {
            DisconnectReason.ClientStopped => 0,
            _ => 4,
        };
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static async Task<int> RunControlAsync(
        CoreClient client,
        ILoggerFactory loggerFactory,
        ILogger logger,
        CancellationToken shutdownToken)
    {
        await using var control = new CaptureControl(client, loggerFactory, logger);
        client.ControlReceived = control.HandleAsync;
        logger.LogInformation("control mode ready: waiting for capture.start");

        var reason = await client.RunAsync(shutdownToken).ConfigureAwait(false);
        await control.StopAsync(CancellationToken.None).ConfigureAwait(false);
        logger.LogInformation("control session ended: {Reason}", reason);
        return reason == DisconnectReason.ClientStopped ? 0 : 4;
    }

    private static async Task<int> RunSimulationAsync(
        CoreClient client,
        SimulationOptions simulation,
        ILoggerFactory loggerFactory,
        ILogger logger,
        CancellationToken shutdownToken)
    {
        var clientTask = client.RunAsync(shutdownToken);

        var streamer = new AudioStreamer(
            client.SendBinaryAsync,
            new SimulatedAudioSource(),
            loggerFactory.CreateLogger<AudioStreamer>(),
            simulation.FramesPerSecond,
            simulation.DropEvery);

        logger.LogInformation(
            "simulating audio: fps={Fps} seconds={Seconds} dropEvery={DropEvery}",
            simulation.FramesPerSecond,
            simulation.Duration.TotalSeconds,
            simulation.DropEvery);

        try
        {
            var duration = simulation.Duration > TimeSpan.Zero ? simulation.Duration : (TimeSpan?)null;
            await streamer.RunAsync(shutdownToken, duration).ConfigureAwait(false);
            await client.CloseAsync(CancellationToken.None).ConfigureAwait(false);
            await clientTask.WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }
        catch (CoreConnectionException exception)
        {
            logger.LogError("audio send failed: {Code}: {Message}", exception.Code, exception.Message);
            return 4;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            // 外部停止：属于正常结束。
        }

        logger.LogInformation("simulation finished: sent={Sent} skipped={Skipped}", streamer.FramesSent, streamer.FramesSkipped);
        Console.Out.WriteLine($"{{\"simulation\":{streamer.ToJson()}}}");
        await client.CloseAsync(CancellationToken.None).ConfigureAwait(false);
        return 0;
    }

    private static async Task<int> RunCaptureAsync(
        CoreClient client,
        CaptureOptions capture,
        SimulationOptions timing,
        ILoggerFactory loggerFactory,
        ILogger logger,
        CancellationToken shutdownToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            logger.LogError("capture is only available on Windows");
            return 2;
        }

        using var duration = timing.Duration > TimeSpan.Zero
            ? new CancellationTokenSource(timing.Duration)
            : null;
        using var linked = duration is null
            ? CancellationTokenSource.CreateLinkedTokenSource(shutdownToken)
            : CancellationTokenSource.CreateLinkedTokenSource(shutdownToken, duration.Token);

        var sources = new List<IAudioFrameSource>();
        if (capture.Loopback)
        {
            sources.Add(new Capture.LoopbackCaptureSource(
                Capture.DeviceCatalog.Resolve(capture.LoopbackDeviceId, NAudio.CoreAudioApi.DataFlow.Render),
                loggerFactory.CreateLogger<Capture.LoopbackCaptureSource>()));
        }

        if (capture.Mic)
        {
            sources.Add(new Capture.MicrophoneCaptureSource(
                Capture.DeviceCatalog.Resolve(capture.MicDeviceId, NAudio.CoreAudioApi.DataFlow.Capture),
                loggerFactory.CreateLogger<Capture.MicrophoneCaptureSource>()));
        }

        var streamers = sources
            .Select(source => new FrameStreamer(source, client.SendBinaryAsync, loggerFactory.CreateLogger<FrameStreamer>()))
            .ToList();

        logger.LogInformation(
            "capturing: loopback={Loopback} mic={Mic} seconds={Seconds}",
            capture.Loopback,
            capture.Mic,
            timing.Duration.TotalSeconds);

        var tasks = streamers
            .Select(streamer => streamer.RunAsync(linked.Token))
            .Append(client.RunAsync(linked.Token))
            .ToList();

        try
        {
            await Task.WhenAll(tasks).ConfigureAwait(false);
        }
        catch (Exception error)
        {
            linked.Cancel();
            try
            {
                await Task.WhenAll(tasks).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // 已记录首个失败原因，其余取消异常忽略。
            }

            if (error is CaptureFailedException captureError)
            {
                logger.LogError("capture failed: {Code}: {Message}", captureError.Code, captureError.Message);
                return 4;
            }

            if (error is CoreConnectionException connectionError)
            {
                logger.LogError("audio send failed: {Code}: {Message}", connectionError.Code, connectionError.Message);
                return 4;
            }

            throw;
        }
        finally
        {
            foreach (var source in sources)
            {
                await source.DisposeAsync().ConfigureAwait(false);
            }
        }

        var sent = streamers.Sum(streamer => streamer.FramesSent);
        var dropped = streamers.Sum(streamer => streamer.FramesDropped);
        logger.LogInformation("capture finished: sent={Sent} dropped={Dropped}", sent, dropped);
        Console.Out.WriteLine($"{{\"capture\":{{\"sent\":{sent},\"dropped\":{dropped}}}}}");
        await client.CloseAsync(CancellationToken.None).ConfigureAwait(false);
        return 0;
    }
}
