using System.Net.WebSockets;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using RealtimeCopilot.AudioCapture.Protocol;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class CoreClientTests
{
    private static AppConfig ConfigFor(string url) => new()
    {
        CoreWsUrl = url,
        Token = new string('a', 64),
        ConnectTimeout = TimeSpan.FromSeconds(5),
        HandshakeTimeout = TimeSpan.FromSeconds(5),
        IdleTimeout = TimeSpan.FromSeconds(10),
    };

    private static async Task SendAsync(WebSocket socket, string text, CancellationToken cancellationToken)
    {
        var bytes = Encoding.UTF8.GetBytes(text);
        await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, cancellationToken);
    }

    private static async Task HoldAsync(WebSocket socket, CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
        }
        catch (OperationCanceledException)
        {
            // 连接被关闭时结束。
        }
    }

    [Fact]
    public async Task Connect_SendsHelloAndAcceptsReady()
    {
        string? receivedToken = null;
        string? receivedRole = null;

        await using var server = await TestCoreServer.StartAsync(async (socket, cancellationToken) =>
        {
            var helloJson = await TestCoreServer.ReceiveTextAsync(socket);
            Assert.NotNull(helloJson);
            var envelope = ProtocolCodec.DeserializeEnvelope(helloJson!);
            Assert.NotNull(envelope);
            Assert.Equal("hello", envelope!.Type);

            var payload = ProtocolCodec.ReadPayload<HelloPayload>(envelope);
            Assert.NotNull(payload);
            receivedToken = payload!.Token;
            receivedRole = payload.Role;

            var ready = ProtocolCodec.Create(
                "system.ready",
                new SystemReadyPayload("0.1.0", ProtocolCodec.Version, ["capture"], null),
                envelope.RequestId);
            await SendAsync(socket, ProtocolCodec.Serialize(ready), cancellationToken);
            await HoldAsync(socket, cancellationToken);
        });

        await using var client = new CoreClient(ConfigFor(server.WebSocketUrl), NullLogger<CoreClient>.Instance);
        var ready = await client.ConnectAsync(CancellationToken.None);

        Assert.Equal(ProtocolCodec.Version, ready.Protocol);
        Assert.Equal("0.1.0", ready.ServerVersion);
        Assert.Equal(new string('a', 64), receivedToken);
        Assert.Equal("audio", receivedRole);
    }

    [Fact]
    public async Task Connect_SurfacesSystemError()
    {
        await using var server = await TestCoreServer.StartAsync(async (socket, cancellationToken) =>
        {
            await TestCoreServer.ReceiveTextAsync(socket);
            var error = ProtocolCodec.Create(
                "system.error",
                new SystemErrorPayload("auth_failed", "invalid token", false));
            await SendAsync(socket, ProtocolCodec.Serialize(error), cancellationToken);
            await socket.CloseAsync(WebSocketCloseStatus.PolicyViolation, "auth failed", cancellationToken);
        });

        await using var client = new CoreClient(ConfigFor(server.WebSocketUrl), NullLogger<CoreClient>.Instance);
        var exception = await Assert.ThrowsAsync<CoreConnectionException>(
            () => client.ConnectAsync(CancellationToken.None));

        Assert.Equal("auth_failed", exception.Code);
    }

    [Fact]
    public async Task Run_AnswersPingWithMatchingNonce()
    {
        var pongNonce = new TaskCompletionSource<string?>(TaskCreationOptions.RunContinuationsAsynchronously);

        await using var server = await TestCoreServer.StartAsync(async (socket, cancellationToken) =>
        {
            var helloJson = await TestCoreServer.ReceiveTextAsync(socket);
            var hello = ProtocolCodec.DeserializeEnvelope(helloJson!)!;
            var ready = ProtocolCodec.Create(
                "system.ready",
                new SystemReadyPayload("0.1.0", ProtocolCodec.Version, [], null),
                hello.RequestId);
            await SendAsync(socket, ProtocolCodec.Serialize(ready), cancellationToken);

            var ping = ProtocolCodec.Create("ping", new HeartbeatPayload("nonce-42"));
            await SendAsync(socket, ProtocolCodec.Serialize(ping), cancellationToken);

            var replyJson = await TestCoreServer.ReceiveTextAsync(socket);
            var reply = ProtocolCodec.DeserializeEnvelope(replyJson!);
            Assert.NotNull(reply);
            Assert.Equal("pong", reply!.Type);
            pongNonce.TrySetResult(ProtocolCodec.ReadPayload<HeartbeatPayload>(reply)?.Nonce);

            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", cancellationToken);
        });

        await using var client = new CoreClient(ConfigFor(server.WebSocketUrl), NullLogger<CoreClient>.Instance);
        await client.ConnectAsync(CancellationToken.None);

        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var reason = await client.RunAsync(timeout.Token);

        Assert.Equal(DisconnectReason.ServerClosed, reason);
        Assert.Equal("nonce-42", await pongNonce.Task);
    }

    [Fact]
    public async Task Run_ReportsClientStoppedOnCancellation()
    {
        await using var server = await TestCoreServer.StartAsync(async (socket, cancellationToken) =>
        {
            var helloJson = await TestCoreServer.ReceiveTextAsync(socket);
            var hello = ProtocolCodec.DeserializeEnvelope(helloJson!)!;
            var ready = ProtocolCodec.Create(
                "system.ready",
                new SystemReadyPayload("0.1.0", ProtocolCodec.Version, [], null),
                hello.RequestId);
            await SendAsync(socket, ProtocolCodec.Serialize(ready), cancellationToken);
            await HoldAsync(socket, cancellationToken);
        });

        await using var client = new CoreClient(ConfigFor(server.WebSocketUrl), NullLogger<CoreClient>.Instance);
        await client.ConnectAsync(CancellationToken.None);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(200));
        var reason = await client.RunAsync(cancellation.Token);

        Assert.Equal(DisconnectReason.ClientStopped, reason);
    }

    [Fact]
    public async Task Connect_FailsWhenNothingIsListening()
    {
        var config = ConfigFor("ws://127.0.0.1:1/ws/v1/events") with
        {
            ConnectTimeout = TimeSpan.FromSeconds(2),
        };
        await using var client = new CoreClient(config, NullLogger<CoreClient>.Instance);
        var exception = await Assert.ThrowsAsync<CoreConnectionException>(
            () => client.ConnectAsync(CancellationToken.None));

        Assert.Contains(exception.Code, new[] { "connect_failed", "connect_timeout" });
    }
}
