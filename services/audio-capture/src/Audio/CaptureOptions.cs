namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>真实采集参数（WASAPI）。</summary>
/// <param name="Loopback">是否采集系统输出。</param>
/// <param name="Mic">是否采集麦克风（默认关闭）。</param>
/// <param name="LoopbackDeviceId">渲染端点 ID；为空表示默认设备。</param>
/// <param name="MicDeviceId">采集端点 ID；为空表示默认设备。</param>
public sealed record CaptureOptions(bool Loopback, bool Mic, string? LoopbackDeviceId, string? MicDeviceId)
{
    /// <summary>是否启用真实采集。</summary>
    public bool Enabled => Loopback || Mic;

    /// <summary>关闭采集。</summary>
    public static readonly CaptureOptions Disabled = new(false, false, null, null);

    /// <summary>解析命令行参数。</summary>
    /// <param name="args">命令行参数。</param>
    /// <returns>解析结果。</returns>
    /// <exception cref="ConfigurationException">参数格式或取值非法。</exception>
    public static CaptureOptions Parse(string[] args)
    {
        ArgumentNullException.ThrowIfNull(args);

        var loopback = false;
        var mic = false;
        string? loopbackDevice = null;
        string? micDevice = null;

        foreach (var argument in args)
        {
            if (argument == "--capture-loopback")
            {
                loopback = true;
                continue;
            }

            if (argument == "--capture-mic")
            {
                mic = true;
                continue;
            }

            if (argument.StartsWith("--capture-device=", StringComparison.Ordinal))
            {
                loopbackDevice = argument["--capture-device=".Length..];
                continue;
            }

            if (argument.StartsWith("--mic-device=", StringComparison.Ordinal))
            {
                micDevice = argument["--mic-device=".Length..];
            }
        }

        return new CaptureOptions(loopback, mic, loopbackDevice, micDevice);
    }
}
