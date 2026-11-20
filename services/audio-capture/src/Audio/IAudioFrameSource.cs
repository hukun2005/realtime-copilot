namespace RealtimeCopilot.AudioCapture.Audio;

/// <summary>采集侧失败：设备丢失、初始化失败或驱动停止。</summary>
/// <param name="Code">稳定错误码，例如 <c>capture_failed</c>、<c>device_lost</c>。</param>
/// <param name="Message">面向诊断的单行说明。</param>
public sealed record CaptureFailure(string Code, string Message);

/// <summary>音频帧来源：真实采集（WASAPI）与模拟源实现同一接口。</summary>
public interface IAudioFrameSource : IAsyncDisposable
{
    /// <summary>来源标识：<c>loopback</c> 或 <c>mic</c>。</summary>
    string Source { get; }

    /// <summary>开始采集。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>启动完成的任务。</returns>
    Task StartAsync(CancellationToken cancellationToken);

    /// <summary>停止采集并释放设备。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>停止完成的任务。</returns>
    Task StopAsync(CancellationToken cancellationToken);

    /// <summary>每产生一帧触发一次；回调运行在采集泵线程。</summary>
    event EventHandler<AudioFrame>? FrameAvailable;

    /// <summary>采集中断时触发。</summary>
    event EventHandler<CaptureFailure>? Failed;
}
