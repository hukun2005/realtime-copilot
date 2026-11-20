using System.Globalization;

namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>
/// 模拟发送的参数：时长、帧率与主动丢帧。
/// </summary>
/// <param name="Enabled">是否启用模拟音频（<c>--simulate-audio</c>）。</param>
/// <param name="Duration">发送时长；<see cref="TimeSpan.Zero"/> 表示直到 Ctrl+C。</param>
/// <param name="FramesPerSecond">帧率，默认 50（20ms 一帧）。</param>
/// <param name="DropEvery">每 N 帧跳过发送一次以制造序号空洞；0 表示不丢帧。</param>
public sealed record SimulationOptions(bool Enabled, TimeSpan Duration, int FramesPerSecond, int DropEvery)
{
    /// <summary>关闭模拟。</summary>
    public static readonly SimulationOptions Disabled = new(false, TimeSpan.Zero, 50, 0);

    /// <summary>解析命令行参数。</summary>
    /// <param name="args">命令行参数（<c>--key=value</c>）。</param>
    /// <returns>解析结果。</returns>
    /// <exception cref="ConfigurationException">参数缺失或越界。</exception>
    public static SimulationOptions Parse(string[] args)
    {
        ArgumentNullException.ThrowIfNull(args);

        var enabled = false;
        var seconds = 0;
        var fps = 50;
        var dropEvery = 0;

        foreach (var argument in args)
        {
            if (argument == "--simulate-audio")
            {
                enabled = true;
                continue;
            }

            if (argument.StartsWith("--seconds=", StringComparison.Ordinal))
            {
                seconds = ParseInt(argument, "--seconds=", 0, 3600);
                continue;
            }

            if (argument.StartsWith("--fps=", StringComparison.Ordinal))
            {
                fps = ParseInt(argument, "--fps=", 1, 200);
                continue;
            }

            if (argument.StartsWith("--drop-every=", StringComparison.Ordinal))
            {
                dropEvery = ParseInt(argument, "--drop-every=", 0, 100_000);
            }
        }

        return new SimulationOptions(enabled, TimeSpan.FromSeconds(seconds), fps, dropEvery);
    }

    private static int ParseInt(string argument, string prefix, int minimum, int maximum)
    {
        var raw = argument[prefix.Length..];
        if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
        {
            throw new ConfigurationException($"{prefix}<n> requires an integer, got '{raw}'");
        }

        if (value < minimum || value > maximum)
        {
            throw new ConfigurationException($"{prefix}<n> must be between {minimum} and {maximum}, got {value}");
        }

        return value;
    }
}
