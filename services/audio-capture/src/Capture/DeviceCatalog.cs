using NAudio.CoreAudioApi;

namespace RealtimeCopilot.AudioCapture.Capture;

/// <summary>一个可选的音频端点。</summary>
/// <param name="Id">系统设备 ID（WASAPI endpoint id）。</param>
/// <param name="FriendlyName">用户可见名称。</param>
/// <param name="Flow">来源：<c>loopback</c> 表示渲染设备，<c>mic</c> 表示采集设备。</param>
public sealed record AudioDeviceInfo(string Id, string FriendlyName, string Flow);

/// <summary>WASAPI 设备枚举。</summary>
/// <remarks>Phase 1 只枚举设备，不打开任何流；采集在 Phase 2 实现。</remarks>
public static class DeviceCatalog
{
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static readonly Lazy<MMDeviceEnumerator> Enumerator = new(() => new MMDeviceEnumerator());

    /// <summary>按设备 ID 解析 WASAPI 端点。</summary>
    /// <param name="deviceId">设备 ID；为空时返回 <c>null</c>（表示系统默认设备）。</param>
    /// <param name="flow">数据流向。</param>
    /// <returns>端点对象；找不到时返回 <c>null</c>。</returns>
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    public static NAudio.CoreAudioApi.MMDevice? Resolve(string? deviceId, NAudio.CoreAudioApi.DataFlow flow)
    {
        if (string.IsNullOrWhiteSpace(deviceId) || !OperatingSystem.IsWindows())
        {
            return null;
        }

        try
        {
            // 枚举器必须活到设备使用结束：提前 Dispose 会让返回的 MMDevice 失效，
            // 用它打开采集会得到静音流。
            return Enumerator.Value.EnumerateAudioEndPoints(flow, DeviceState.Active)
                .FirstOrDefault(device => string.Equals(device.ID, deviceId, StringComparison.Ordinal));
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            return null;
        }
    }

    /// <summary>列出当前活动端点。</summary>
    /// <returns>设备列表；非 Windows 平台或枚举失败时返回空列表。</returns>
    public static IReadOnlyList<AudioDeviceInfo> List()
    {
        if (!OperatingSystem.IsWindows())
        {
            return [];
        }

        try
        {
            using var enumerator = new MMDeviceEnumerator();
            var devices = new List<AudioDeviceInfo>();
            foreach (var (flow, label) in new[] { (DataFlow.Render, "loopback"), (DataFlow.Capture, "mic") })
            {
                foreach (var device in enumerator.EnumerateAudioEndPoints(flow, DeviceState.Active))
                {
                    devices.Add(new AudioDeviceInfo(device.ID, device.FriendlyName, label));
                }
            }

            return devices;
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            // 设备枚举失败不应让进程崩溃；Phase 2 会把它变成可上报的错误状态。
            return [];
        }
    }
}
