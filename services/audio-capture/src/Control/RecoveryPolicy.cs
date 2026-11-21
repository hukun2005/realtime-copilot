namespace RealtimeCopilot.AudioCapture.Control;

/// <summary>
/// 设备恢复策略：最多三次尝试，退避 500ms / 1s / 2s。
/// </summary>
public static class RecoveryPolicy
{
    /// <summary>最大尝试次数。</summary>
    public const int MaxAttempts = 3;

    private static readonly TimeSpan[] DelayByAttempt =
    [
        TimeSpan.FromMilliseconds(500),
        TimeSpan.FromSeconds(1),
        TimeSpan.FromSeconds(2),
    ];

    /// <summary>第 <paramref name="attempt"/> 次尝试（从 1 开始）前的退避时间。</summary>
    /// <param name="attempt">尝试序号，从 1 开始；超出范围时按边界处理。</param>
    /// <returns>退避时长。</returns>
    public static TimeSpan DelayFor(int attempt)
    {
        var index = Math.Clamp(attempt, 1, MaxAttempts) - 1;
        return DelayByAttempt[index];
    }
}
