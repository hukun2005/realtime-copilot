using System.Collections;
using System.Globalization;
using System.Text.Json;

namespace RealtimeCopilot.AudioCapture;

/// <summary>配置非法；进程应以退出码 2 结束。</summary>
public sealed class ConfigurationException : Exception
{
    /// <summary>创建异常。</summary>
    /// <param name="message">面向用户的单行说明。</param>
    public ConfigurationException(string message)
        : base(message)
    {
    }
}

/// <summary>连接 Core 所需配置。优先级：命令行 &gt; 环境变量 &gt; runtime.json &gt; appsettings.json &gt; 默认值。</summary>
public sealed record AppConfig
{
    /// <summary>Core 的 WebSocket 地址；必须是回环地址。</summary>
    public required string CoreWsUrl { get; init; }

    /// <summary>握手令牌。</summary>
    public required string Token { get; init; }

    /// <summary>连接角色；音频进程固定为 <c>audio</c>。</summary>
    public string Role { get; init; } = "audio";

    /// <summary>客户端名称，写入 hello 便于服务端日志。</summary>
    public string ClientName { get; init; } = "audio-capture";

    /// <summary>客户端版本。</summary>
    public string ClientVersion { get; init; } = "0.1.0";

    /// <summary>TCP/WebSocket 建立超时。</summary>
    public TimeSpan ConnectTimeout { get; init; } = TimeSpan.FromSeconds(5);

    /// <summary>hello → system.ready 的超时。</summary>
    public TimeSpan HandshakeTimeout { get; init; } = TimeSpan.FromSeconds(3);

    /// <summary>无任何入站消息的超时（服务端每 15s 发 ping）。</summary>
    public TimeSpan IdleTimeout { get; init; } = TimeSpan.FromSeconds(45);

    /// <summary>单条入站消息上限。</summary>
    public int MaxMessageBytes { get; init; } = 262_144;

    /// <summary>日志级别：debug/info/warning/error。</summary>
    public string LogLevel { get; init; } = "info";

    /// <summary>实际使用的 runtime.json 路径（可能不存在）。</summary>
    public string? RuntimePath { get; init; }

    /// <summary>加载配置。</summary>
    /// <param name="args">命令行参数，支持 <c>--key=value</c>。</param>
    /// <param name="environment">环境变量表。</param>
    /// <param name="workingDirectory">用于查找 appsettings.json；默认当前目录。</param>
    /// <returns>校验后的配置。</returns>
    /// <exception cref="ConfigurationException">配置缺失或非法。</exception>
    public static AppConfig Load(string[] args, IDictionary environment, string? workingDirectory = null)
    {
        ArgumentNullException.ThrowIfNull(args);
        ArgumentNullException.ThrowIfNull(environment);

        var arguments = ParseArguments(args);
        var baseDirectory = workingDirectory ?? Directory.GetCurrentDirectory();

        var settings = ReadAppSettings(baseDirectory);

        var runtimePath = FirstNonEmpty(
            GetArgument(arguments, "runtime"),
            Get(environment, "RTC_RUNTIME_FILE"));
        var dataDirectory = FirstNonEmpty(Get(environment, "RTC_DATA_DIR"), DefaultDataDirectory())
            ?? DefaultDataDirectory();
        runtimePath ??= Path.Combine(dataDirectory, "runtime.json");

        string? runtimeUrl = null;
        string? runtimeToken = null;
        if (File.Exists(runtimePath))
        {
            (runtimeUrl, runtimeToken) = ReadRuntimeFile(runtimePath);
        }

        var wsUrl = FirstNonEmpty(
            GetArgument(arguments, "ws-url"),
            Get(environment, "RTC_CORE_WS_URL"),
            runtimeUrl,
            settings.GetValueOrDefault("wsUrl"));

        var token = FirstNonEmpty(
            GetArgument(arguments, "token"),
            Get(environment, "RTC_CORE_TOKEN"),
            runtimeToken,
            settings.GetValueOrDefault("token"));

        if (string.IsNullOrWhiteSpace(wsUrl))
        {
            throw new ConfigurationException(
                $"core WebSocket URL is not configured (set --ws-url, RTC_CORE_WS_URL, or start the core to write {runtimePath})");
        }

        if (string.IsNullOrWhiteSpace(token))
        {
            throw new ConfigurationException(
                $"core token is not configured (set --token, RTC_CORE_TOKEN, or start the core to write {runtimePath})");
        }

        ValidateLoopback(wsUrl);

        if (token.Length < 16)
        {
            throw new ConfigurationException("core token looks too short; expected the 64-character core token");
        }

        return new AppConfig
        {
            CoreWsUrl = wsUrl,
            Token = token,
            Role = FirstNonEmpty(GetArgument(arguments, "role"), settings.GetValueOrDefault("role")) ?? "audio",
            ClientName = FirstNonEmpty(settings.GetValueOrDefault("clientName")) ?? "audio-capture",
            LogLevel = FirstNonEmpty(
                GetArgument(arguments, "log-level"),
                Get(environment, "RTC_LOG_LEVEL"),
                settings.GetValueOrDefault("logLevel")) ?? "info",
            ConnectTimeout = ReadTimeout(environment, "RTC_CONNECT_TIMEOUT_MS", TimeSpan.FromSeconds(5)),
            IdleTimeout = ReadTimeout(environment, "RTC_IDLE_TIMEOUT_MS", TimeSpan.FromSeconds(45)),
            RuntimePath = runtimePath,
        };
    }

    /// <summary>默认数据目录：Windows 为 <c>%LOCALAPPDATA%\RealTimeCopilot</c>，其他平台为 XDG 数据目录。</summary>
    /// <returns>绝对路径。</returns>
    public static string DefaultDataDirectory()
    {
        if (OperatingSystem.IsWindows())
        {
            var local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            var root = string.IsNullOrWhiteSpace(local)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "Local")
                : local!;
            return Path.Combine(root, "RealTimeCopilot");
        }

        var xdg = Environment.GetEnvironmentVariable("XDG_DATA_HOME");
        var baseDirectory = string.IsNullOrWhiteSpace(xdg)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share")
            : xdg;
        return Path.Combine(baseDirectory, "realtime-copilot");
    }

    private static void ValidateLoopback(string wsUrl)
    {
        if (!Uri.TryCreate(wsUrl, UriKind.Absolute, out var uri) || uri.Scheme != "ws")
        {
            throw new ConfigurationException("core WebSocket URL must be an absolute ws:// URL");
        }

        if (uri.Host is not ("127.0.0.1" or "::1"))
        {
            throw new ConfigurationException($"core WebSocket URL must target the loopback address, got '{uri.Host}'");
        }
    }

    private static Dictionary<string, string?> ParseArguments(string[] args)
    {
        var parsed = new Dictionary<string, string?>(StringComparer.Ordinal);
        foreach (var argument in args)
        {
            if (!argument.StartsWith("--", StringComparison.Ordinal))
            {
                throw new ConfigurationException($"unexpected argument '{argument}'; use --key=value");
            }

            var separator = argument.IndexOf('=', StringComparison.Ordinal);
            if (separator < 0)
            {
                throw new ConfigurationException($"argument '{argument}' must use --key=value form");
            }

            var key = argument[2..separator];
            parsed[key] = argument[(separator + 1)..];
        }

        return parsed;
    }

    private static (string? Url, string? Token) ReadRuntimeFile(string path)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var url = root.TryGetProperty("wsUrl", out var urlElement) ? urlElement.GetString() : null;
            var token = root.TryGetProperty("token", out var tokenElement) ? tokenElement.GetString() : null;
            return (url, token);
        }
        catch (Exception exception) when (exception is IOException or JsonException or UnauthorizedAccessException)
        {
            throw new ConfigurationException($"cannot read runtime file '{path}': {exception.Message}");
        }
    }

    private static Dictionary<string, string?> ReadAppSettings(string directory)
    {
        var path = Path.Combine(directory, "appsettings.json");
        if (!File.Exists(path))
        {
            return [];
        }

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var result = new Dictionary<string, string?>(StringComparer.Ordinal);
            if (root.TryGetProperty("core", out var core))
            {
                result["wsUrl"] = core.TryGetProperty("wsUrl", out var url) ? url.GetString() : null;
                result["token"] = core.TryGetProperty("token", out var token) ? token.GetString() : null;
                result["role"] = core.TryGetProperty("role", out var role) ? role.GetString() : null;
                result["clientName"] = core.TryGetProperty("clientName", out var clientName) ? clientName.GetString() : null;
            }

            if (root.TryGetProperty("log", out var log))
            {
                result["logLevel"] = log.TryGetProperty("level", out var level) ? level.GetString() : null;
            }

            return result;
        }
        catch (Exception exception) when (exception is IOException or JsonException or UnauthorizedAccessException)
        {
            throw new ConfigurationException($"cannot read appsettings.json: {exception.Message}");
        }
    }

    private static TimeSpan ReadTimeout(IDictionary environment, string key, TimeSpan fallback)
    {
        var raw = Get(environment, key);
        if (string.IsNullOrWhiteSpace(raw))
        {
            return fallback;
        }

        if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var milliseconds) || milliseconds <= 0)
        {
            throw new ConfigurationException($"{key} must be a positive integer number of milliseconds");
        }

        return TimeSpan.FromMilliseconds(milliseconds);
    }

    private static string? Get(IDictionary environment, string key)
    {
        var value = environment[key];
        return value switch
        {
            null => null,
            string text => text,
            _ => Convert.ToString(value, CultureInfo.InvariantCulture),
        };
    }

    private static string? GetArgument(IReadOnlyDictionary<string, string?> arguments, string key) =>
        arguments.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value) ? value : null;

    private static string? FirstNonEmpty(params string?[] values) =>
        values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));
}
