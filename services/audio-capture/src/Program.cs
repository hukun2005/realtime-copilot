using Microsoft.Extensions.Logging;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture;

/// <summary>进程入口。</summary>
/// <remarks>
/// 退出码：0 正常退出；2 配置非法；3 无法连接 Core；4 连接异常结束或发送失败。
/// </remarks>
internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        AppConfig config;
        SimulationOptions simulation;
        try
        {
            config = AppConfig.Load(args, Environment.GetEnvironmentVariables());
            simulation = SimulationOptions.Parse(args);
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

        if (simulation.Enabled)
        {
            return await RunSimulationAsync(client, simulation, loggerFactory, logger, shutdown.Token).ConfigureAwait(false);
        }

        var reason = await client.RunAsync(shutdown.Token).ConfigureAwait(false);
        logger.LogInformation("stopped: {Reason}", reason);
        return reason switch
        {
            DisconnectReason.ClientStopped => 0,
            _ => 4,
        };
    }

    private static async Task<int> RunSimulationAsync(
        CoreClient client,
        SimulationOptions simulation,
        ILoggerFactory loggerFactory,
        ILogger logger,
        CancellationToken shutdownToken)
    {
        using var duration = simulation.Duration > TimeSpan.Zero
            ? new CancellationTokenSource(simulation.Duration)
            : null;
        using var linked = duration is null
            ? CancellationTokenSource.CreateLinkedTokenSource(shutdownToken)
            : CancellationTokenSource.CreateLinkedTokenSource(shutdownToken, duration.Token);

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
            await Task.WhenAll(
                streamer.RunAsync(linked.Token),
                client.RunAsync(linked.Token)).ConfigureAwait(false);
        }
        catch (CoreConnectionException exception)
        {
            logger.LogError("audio send failed: {Code}: {Message}", exception.Code, exception.Message);
            return 4;
        }

        logger.LogInformation("simulation finished: sent={Sent} skipped={Skipped}", streamer.FramesSent, streamer.FramesSkipped);
        Console.Out.WriteLine($"{{\"simulation\":{streamer.ToJson()}}}");
        await client.CloseAsync(CancellationToken.None).ConfigureAwait(false);
        return 0;
    }
}
