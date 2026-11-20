using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Console;

namespace RealtimeCopilot.AudioCapture;

/// <summary>JSON Lines 日志：单行一条，字段固定，便于与 Core 的日志同一格式采集。</summary>
public static class LoggingSetup
{
    /// <summary>创建日志工厂。</summary>
    /// <param name="level">级别名：debug/info/warning/error，非法值按 info 处理。</param>
    /// <returns>可直接使用的工厂；调用方负责释放。</returns>
    public static ILoggerFactory Create(string level)
    {
        var minimum = ParseLevel(level);
        return LoggerFactory.Create(builder =>
        {
            builder.ClearProviders();
            builder.SetMinimumLevel(minimum);
            builder.AddJsonConsole(options =>
            {
                options.IncludeScopes = false;
                options.UseUtcTimestamp = true;
                options.TimestampFormat = "yyyy-MM-ddTHH:mm:ss.fffZ";
            });
        });
    }

    /// <summary>把级别名解析为 <see cref="LogLevel"/>。</summary>
    /// <param name="level">级别名。</param>
    /// <returns>日志级别。</returns>
    public static LogLevel ParseLevel(string level) => level?.Trim().ToLowerInvariant() switch
    {
        "debug" => LogLevel.Debug,
        "warning" or "warn" => LogLevel.Warning,
        "error" => LogLevel.Error,
        _ => LogLevel.Information,
    };
}
