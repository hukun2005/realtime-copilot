using System.Collections;
using System.Text.Json;
using RealtimeCopilot.AudioCapture;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class AppConfigTests : IDisposable
{
    private readonly string _directory = Path.Combine(Path.GetTempPath(), "rtc-config-" + Guid.NewGuid().ToString("N"));

    public AppConfigTests()
    {
        Directory.CreateDirectory(_directory);
    }

    public void Dispose()
    {
        Directory.Delete(_directory, recursive: true);
    }

    private static Dictionary<string, string?> EmptyEnvironment() => [];

    [Fact]
    public void Load_ReadsRuntimeFile()
    {
        var runtimePath = Path.Combine(_directory, "runtime.json");
        File.WriteAllText(
            runtimePath,
            JsonSerializer.Serialize(
                new
                {
                    wsUrl = "ws://127.0.0.1:41234/ws/v1/events",
                    token = new string('a', 64),
                }));

        var environment = new Hashtable { ["RTC_DATA_DIR"] = _directory };
        var config = AppConfig.Load([], environment, _directory);

        Assert.Equal("ws://127.0.0.1:41234/ws/v1/events", config.CoreWsUrl);
        Assert.Equal(new string('a', 64), config.Token);
        Assert.Equal(runtimePath, config.RuntimePath);
    }

    [Fact]
    public void Load_ArgumentsOverrideEnvironment()
    {
        var environment = new Hashtable
        {
            ["RTC_CORE_WS_URL"] = "ws://127.0.0.1:1/ws/v1/events",
            ["RTC_CORE_TOKEN"] = new string('b', 64),
            ["RTC_LOG_LEVEL"] = "debug",
        };

        var config = AppConfig.Load(
            [
                "--ws-url=ws://127.0.0.1:2/ws/v1/events",
                "--token=" + new string('c', 64),
                "--log-level=error",
            ],
            environment,
            _directory);

        Assert.Equal("ws://127.0.0.1:2/ws/v1/events", config.CoreWsUrl);
        Assert.Equal(new string('c', 64), config.Token);
        Assert.Equal("error", config.LogLevel);
    }

    [Theory]
    [InlineData("ws://10.0.0.5:1234/ws")]
    [InlineData("ws://localhost:1234/ws")]
    [InlineData("http://127.0.0.1:1234/ws")]
    [InlineData("127.0.0.1:1234")]
    public void Load_RejectsNonLoopbackOrNonWsUrls(string url)
    {
        var environment = new Hashtable { ["RTC_CORE_WS_URL"] = url, ["RTC_CORE_TOKEN"] = new string('a', 64) };
        var error = Assert.Throws<ConfigurationException>(() => AppConfig.Load([], environment, _directory));
        Assert.True(
            error.Message.Contains("loopback", StringComparison.OrdinalIgnoreCase)
                || error.Message.Contains("absolute ws://", StringComparison.Ordinal),
            error.Message);
    }

    [Fact]
    public void Load_RejectsShortToken()
    {
        var environment = new Hashtable
        {
            ["RTC_CORE_WS_URL"] = "ws://127.0.0.1:1234/ws",
            ["RTC_CORE_TOKEN"] = "short",
        };
        Assert.Throws<ConfigurationException>(() => AppConfig.Load([], environment, _directory));
    }

    [Fact]
    public void Load_WithoutAnySource_Fails()
    {
        var error = Assert.Throws<ConfigurationException>(() => AppConfig.Load([], EmptyEnvironment(), _directory));
        Assert.Contains("WebSocket URL", error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Load_RejectsMalformedArgument()
    {
        Assert.Throws<ConfigurationException>(() => AppConfig.Load(["--ws-url"], EmptyEnvironment(), _directory));
        Assert.Throws<ConfigurationException>(() => AppConfig.Load(["positional"], EmptyEnvironment(), _directory));
    }

    [Fact]
    public void Load_ReadsAppSettings()
    {
        File.WriteAllText(
            Path.Combine(_directory, "appsettings.json"),
            """
            {
              "core": { "wsUrl": "ws://127.0.0.1:9999/ws/v1/events", "token": "0123456789abcdef0123456789abcdef" },
              "log": { "level": "warning" }
            }
            """);

        var config = AppConfig.Load([], EmptyEnvironment(), _directory);
        Assert.Equal("ws://127.0.0.1:9999/ws/v1/events", config.CoreWsUrl);
        Assert.Equal("warning", config.LogLevel);
    }
}
