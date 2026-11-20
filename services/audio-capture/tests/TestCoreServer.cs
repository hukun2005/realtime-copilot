using System.Net.WebSockets;
using System.Text;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using RealtimeCopilot.AudioCapture;

namespace RealtimeCopilot.AudioCapture.Tests;

/// <summary>内置的真实 Kestrel WebSocket 服务，用于驱动 <see cref="CoreClient"/>。</summary>
internal sealed class TestCoreServer : IAsyncDisposable
{
    private readonly WebApplication _app;

    private TestCoreServer(WebApplication app, string webSocketUrl)
    {
        _app = app;
        WebSocketUrl = webSocketUrl;
    }

    /// <summary>客户端应连接到的地址。</summary>
    public string WebSocketUrl { get; }

    /// <summary>启动服务。</summary>
    /// <param name="handler">每条连接的处理器。</param>
    /// <returns>已监听的服务。</returns>
    public static async Task<TestCoreServer> StartAsync(Func<WebSocket, CancellationToken, Task> handler)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseUrls("http://127.0.0.1:0");
        builder.Logging.ClearProviders();

        var app = builder.Build();
        app.UseWebSockets();
        app.Map(
            "/ws",
            async (HttpContext context) =>
            {
                if (!context.WebSockets.IsWebSocketRequest)
                {
                    context.Response.StatusCode = StatusCodes.Status400BadRequest;
                    return;
                }

                using var socket = await context.WebSockets.AcceptWebSocketAsync();
                await handler(socket, context.RequestAborted);
            });

        await app.StartAsync().ConfigureAwait(false);
        var address = app.Urls.First();
        var url = address.Replace("http://", "ws://", StringComparison.Ordinal) + "/ws";
        return new TestCoreServer(app, url);
    }

    /// <summary>读取一条文本消息。</summary>
    /// <param name="socket">连接。</param>
    /// <returns>消息文本；对端关闭时为 <c>null</c>。</returns>
    public static async Task<string?> ReceiveTextAsync(WebSocket socket)
    {
        var buffer = new byte[16 * 1024];
        using var accumulator = new MemoryStream();
        while (true)
        {
            var result = await socket.ReceiveAsync(buffer, CancellationToken.None);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }

            accumulator.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                return Encoding.UTF8.GetString(accumulator.ToArray());
            }
        }
    }

    /// <summary>读取一条二进制消息。</summary>
    /// <param name="socket">连接。</param>
    /// <returns>消息字节；对端关闭时为 <c>null</c>。</returns>
    public static async Task<byte[]?> ReceiveBinaryAsync(WebSocket socket)
    {
        var buffer = new byte[16 * 1024];
        using var accumulator = new MemoryStream();
        while (true)
        {
            var result = await socket.ReceiveAsync(buffer, CancellationToken.None);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }

            accumulator.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                return accumulator.ToArray();
            }
        }
    }

    /// <inheritdoc />
    public async ValueTask DisposeAsync()
    {
        await _app.StopAsync().ConfigureAwait(false);
        await _app.DisposeAsync().ConfigureAwait(false);
    }
}
