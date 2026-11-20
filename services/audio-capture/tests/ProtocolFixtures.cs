namespace RealtimeCopilot.AudioCapture.Tests;

/// <summary>定位仓库内的协议 fixtures，供跨语言一致性测试使用。</summary>
internal static class ProtocolFixtures
{
    /// <summary>fixtures 目录的绝对路径。</summary>
    public static string Directory { get; } = Locate();

    private static string Locate()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null)
        {
            var candidate = Path.Combine(current.FullName, "packages", "protocol", "fixtures");
            if (System.IO.Directory.Exists(candidate))
            {
                return candidate;
            }

            current = current.Parent;
        }

        throw new DirectoryNotFoundException(
            $"could not locate packages/protocol/fixtures above {AppContext.BaseDirectory}");
    }
}
