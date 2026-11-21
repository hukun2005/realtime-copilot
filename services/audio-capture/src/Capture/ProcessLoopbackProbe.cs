using System.Diagnostics;

namespace RealtimeCopilot.AudioCapture.Capture;

/// <summary>
/// 进程级 loopback 能力探测结果。
/// </summary>
/// <remarks>
/// 只声明本机可验证的事实；真实目标应用（腾讯会议 / 微信）的音频捕获一律标记 NOT_VERIFIED。
/// </remarks>
public sealed record ProcessLoopbackProbeReport(
    int WindowsBuild,
    bool BuildSupported,
    bool ApiPresent,
    string ApiDetail,
    int? TargetPid,
    bool? TargetPidValid,
    string ChildProcesses,
    string ExitBehavior,
    string RealAudio,
    string TeamsMeeting,
    string WeChatPc);

/// <summary>按 Windows Build 与 API 存在性判断进程级 loopback 能力。</summary>
public static class ProcessLoopbackProbe
{
    /// <summary>微软官方文档给出的最低 Build。</summary>
    public const int MinimumBuild = 20348;

    /// <summary>探测本机能力；不启动采集、不修改任何设备状态。</summary>
    /// <param name="targetPid">可选的目标进程 ID。</param>
    /// <returns>探测报告。</returns>
    public static ProcessLoopbackProbeReport Probe(int? targetPid)
    {
        var build = Environment.OSVersion.Version.Build;
        var apiPresent = DetectApi();
        var pidValid = targetPid is null ? (bool?)null : IsProcessAlive(targetPid.Value);

        return new ProcessLoopbackProbeReport(
            build,
            build >= MinimumBuild,
            apiPresent,
            apiPresent ? "WasapiRecorderBuilder exposes process-loopback API" : "process-loopback API not found in this NAudio build",
            targetPid,
            pidValid,
            "NOT_VERIFIED",
            "NOT_VERIFIED",
            "NOT_VERIFIED",
            "NOT_VERIFIED",
            "NOT_VERIFIED");
    }

    private static bool DetectApi()
    {
        try
        {
            var methods = typeof(NAudio.Wave.WasapiRecorderBuilder).GetMethods();
            return methods.Any(method => method.Name.Contains("ProcessLoopback", StringComparison.OrdinalIgnoreCase));
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static bool IsProcessAlive(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
