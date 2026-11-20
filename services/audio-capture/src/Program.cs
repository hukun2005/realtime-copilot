using Microsoft.Extensions.Logging;

namespace RealtimeCopilot.AudioCapture;

/// <summary>进程入口。</summary>
/// <remarks>
/// 退出码：0 正常退出；2 配置非法；3 无法连接 Core；4 连接异常结束。
/// </remarks>
internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        AppConfig config;
        try
        {
            config = AppConfig.Load(args, Environment.GetEnvironmentVariables());
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

        var reason = await client.RunAsync(shutdown.Token).ConfigureAwait(false);
        logger.LogInformation("stopped: {Reason}", reason);
        return reason switch
        {
            DisconnectReason.ClientStopped => 0,
            _ => 4,
        };
    }
}
