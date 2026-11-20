namespace RealtimeCopilot.AudioCapture.Capture;

/// <summary>一帧 16kHz / 单声道 / PCM16LE 音频。</summary>
/// <param name="Pcm">PCM 字节。</param>
/// <param name="CaptureTimestampNs">该帧最后一个采样点的单调时钟（纳秒）。</param>
/// <param name="Source">来源：<c>loopback</c> 或 <c>mic</c>。</param>
public readonly record struct AudioFrame(ReadOnlyMemory<byte> Pcm, long CaptureTimestampNs, string Source);

/// <summary>音频帧传输接口：Phase 2 的 WASAPI 实现与 Core 的二进制帧通道都按它对接。</summary>
public interface IAudioFrameSource : IAsyncDisposable
{
    /// <summary>来源标识：<c>loopback</c> 或 <c>mic</c>。</summary>
    string Source { get; }

    /// <summary>开始采集。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>表示启动完成的任务。</returns>
    Task StartAsync(CancellationToken cancellationToken);

    /// <summary>停止采集并释放设备。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>表示停止完成的任务。</returns>
    Task StopAsync(CancellationToken cancellationToken);

    /// <summary>每产生一帧触发一次。</summary>
    event EventHandler<AudioFrame>? FrameAvailable;
}
