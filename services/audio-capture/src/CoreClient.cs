using System.Net.WebSockets;
using System.Text;
using Microsoft.Extensions.Logging;
using RealtimeCopilot.AudioCapture.Protocol;

namespace RealtimeCopilot.AudioCapture;

/// <summary>连接 Core 失败，或握手被拒绝。</summary>
public sealed class CoreConnectionException : Exception
{
    /// <summary>创建异常。</summary>
    /// <param name="code">稳定的机器可路由代码。</param>
    /// <param name="message">面向诊断的说明。</param>
    /// <param name="inner">底层异常（可选）。</param>
    public CoreConnectionException(string code, string message, Exception? inner = null)
        : base(message, inner)
    {
        Code = code;
    }

    /// <summary>稳定错误码，例如 <c>auth_failed</c> 或 <c>connect_timeout</c>。</summary>
    public string Code { get; }
}

/// <summary>连接结束的原因。</summary>
public enum DisconnectReason
{
    /// <summary>本进程主动停止（Ctrl+C）。</summary>
    ClientStopped,

    /// <summary>Core 主动关闭。</summary>
    ServerClosed,

    /// <summary>长时间没有任何入站消息。</summary>
    IdleTimeout,

    /// <summary>底层 WebSocket 错误。</summary>
    SocketError,
}

/// <summary>Core 的 WebSocket 客户端：握手、心跳应答、断线识别与优雅关闭。</summary>
public sealed class CoreClient : IAsyncDisposable
{
    private readonly AppConfig _config;
    private readonly ILogger<CoreClient> _logger;
    private readonly ClientWebSocket _socket = new();
    private readonly byte[] _buffer = new byte[16 * 1024];
    private bool _disposed;

    /// <summary>创建客户端。</summary>
    /// <param name="config">连接配置。</param>
    /// <param name="logger">日志。</param>
    public CoreClient(AppConfig config, ILogger<CoreClient> logger)
    {
        _config = config ?? throw new ArgumentNullException(nameof(config));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        _socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(20);
    }

    /// <summary>建立连接并完成 hello 握手。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>Core 返回的 system.ready 载荷。</returns>
    /// <exception cref="CoreConnectionException">连接、握手或鉴权失败。</exception>
    public async Task<SystemReadyPayload> ConnectAsync(CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        using var connectTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        connectTimeout.CancelAfter(_config.ConnectTimeout);
        try
        {
            await _socket.ConnectAsync(new Uri(_config.CoreWsUrl), connectTimeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new CoreConnectionException("connect_timeout", $"could not connect to {_config.CoreWsUrl} within {_config.ConnectTimeout}");
        }
        catch (WebSocketException exception)
        {
            throw new CoreConnectionException("connect_failed", $"could not connect to {_config.CoreWsUrl}: {exception.Message}", exception);
        }

        var hello = ProtocolCodec.Create(
            "hello",
            new HelloPayload(_config.Token, _config.Role, _config.ClientVersion, _config.ClientName),
            ProtocolCodec.NewRequestId());
        await SendAsync(hello, cancellationToken).ConfigureAwait(false);

        using var handshakeTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        handshakeTimeout.CancelAfter(_config.HandshakeTimeout);

        while (true)
        {
            string? message;
            try
            {
                message = await ReceiveTextAsync(handshakeTimeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                throw new CoreConnectionException("handshake_timeout", "core did not answer hello in time");
            }

            if (message is null)
            {
                throw new CoreConnectionException("closed_during_handshake", "core closed the connection during the handshake");
            }

            var envelope = ProtocolCodec.DeserializeEnvelope(message)
                ?? throw new CoreConnectionException("invalid_envelope", "core sent malformed JSON");

            switch (envelope.Type)
            {
                case "system.ready":
                {
                    var ready = ProtocolCodec.ReadPayload<SystemReadyPayload>(envelope)
                        ?? throw new CoreConnectionException("invalid_payload", "system.ready payload was invalid");
                    if (ready.Protocol != ProtocolCodec.Version)
                    {
                        throw new CoreConnectionException(
                            "protocol_mismatch",
                            $"core speaks protocol {ready.Protocol}, client speaks {ProtocolCodec.Version}");
                    }

                    _logger.LogInformation(
                        "core ready: server={ServerVersion} protocol={Protocol} capabilities={Capabilities}",
                        ready.ServerVersion,
                        ready.Protocol,
                        string.Join(',', ready.Capabilities));
                    return ready;
                }

                case "system.error":
                {
                    var error = ProtocolCodec.ReadPayload<SystemErrorPayload>(envelope);
                    throw new CoreConnectionException(
                        error?.Code ?? "system_error",
                        error?.Message ?? "core rejected the connection");
                }

                default:
                    // 前向兼容：未知事件忽略，继续等待 ready。
                    _logger.LogDebug("ignoring {Type} during handshake", envelope.Type);
                    continue;
            }
        }
    }

    /// <summary>进入接收循环，直到被取消或连接结束。</summary>
    /// <param name="cancellationToken">取消令牌（Ctrl+C）。</param>
    /// <returns>连接结束的原因。</returns>
    public async Task<DisconnectReason> RunAsync(CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        while (true)
        {
            using var idleTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            idleTimeout.CancelAfter(_config.IdleTimeout);

            string? message;
            try
            {
                message = await ReceiveTextAsync(idleTimeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                await CloseQuietlyAsync().ConfigureAwait(false);
                return DisconnectReason.ClientStopped;
            }
            catch (OperationCanceledException)
            {
                _logger.LogWarning("no traffic from core for {IdleTimeout}", _config.IdleTimeout);
                await CloseQuietlyAsync().ConfigureAwait(false);
                return DisconnectReason.IdleTimeout;
            }
            catch (WebSocketException exception)
            {
                _logger.LogWarning(exception, "core socket failed");
                return DisconnectReason.SocketError;
            }

            if (message is null)
            {
                _logger.LogInformation("core closed the connection");
                return DisconnectReason.ServerClosed;
            }

            await HandleIncomingAsync(message, cancellationToken).ConfigureAwait(false);
        }
    }

    /// <summary>发送一帧音频（Binary Message）。</summary>
    /// <param name="payload">已编码的帧字节。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>发送完成的任务。</returns>
    /// <exception cref="CoreConnectionException">连接已关闭或发送失败。</exception>
    public async Task SendBinaryAsync(ReadOnlyMemory<byte> payload, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        if (_socket.State != WebSocketState.Open)
        {
            throw new CoreConnectionException("send_failed", "the core connection is not open");
        }

        try
        {
            await _socket.SendAsync(payload, WebSocketMessageType.Binary, endOfMessage: true, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is WebSocketException or ObjectDisposedException or InvalidOperationException)
        {
            throw new CoreConnectionException("send_failed", $"could not send audio frame: {exception.Message}", exception);
        }
    }

    /// <summary>发送普通关闭帧。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>表示关闭流程的任务。</returns>
    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        if (_socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(2));
            try
            {
                await _socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "client shutdown", timeout.Token).ConfigureAwait(false);
            }
            catch (Exception exception) when (exception is WebSocketException or OperationCanceledException)
            {
                _logger.LogDebug(exception, "closing the socket did not complete cleanly");
            }
        }
    }

    /// <inheritdoc />
    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _socket.Dispose();
        await ValueTask.CompletedTask.ConfigureAwait(false);
    }

    private async Task HandleIncomingAsync(string message, CancellationToken cancellationToken)
    {
        var envelope = ProtocolCodec.DeserializeEnvelope(message);
        if (envelope is null)
        {
            _logger.LogWarning("received malformed JSON from core");
            return;
        }

        switch (envelope.Type)
        {
            case "ping":
            {
                var ping = ProtocolCodec.ReadPayload<HeartbeatPayload>(envelope);
                var pong = ProtocolCodec.Create("pong", new HeartbeatPayload(ping?.Nonce), envelope.RequestId);
                await SendAsync(pong, cancellationToken).ConfigureAwait(false);
                return;
            }

            case "system.error":
            {
                var error = ProtocolCodec.ReadPayload<SystemErrorPayload>(envelope);
                _logger.LogWarning(
                    "core reported {Code}: {Message} (retryable={Retryable})",
                    error?.Code,
                    error?.Message,
                    error?.Retryable);
                return;
            }

            case "system.ready":
                _logger.LogDebug("ignoring duplicate system.ready");
                return;

            default:
                _logger.LogDebug("ignoring unsupported event {Type}", envelope.Type);
                return;
        }
    }

    private async Task SendAsync<TPayload>(EventEnvelope<TPayload> envelope, CancellationToken cancellationToken)
    {
        var payload = Encoding.UTF8.GetBytes(ProtocolCodec.Serialize(envelope));
        await _socket.SendAsync(payload, WebSocketMessageType.Text, endOfMessage: true, cancellationToken).ConfigureAwait(false);
    }

    private async Task<string?> ReceiveTextAsync(CancellationToken cancellationToken)
    {
        using var accumulator = new MemoryStream();
        while (true)
        {
            var result = await _socket.ReceiveAsync(_buffer, cancellationToken).ConfigureAwait(false);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }

            if (result.MessageType != WebSocketMessageType.Text)
            {
                // 前向兼容：二进制帧在 Phase 2 才有语义，这里整体忽略。
                continue;
            }

            accumulator.Write(_buffer, 0, result.Count);
            if (accumulator.Length > _config.MaxMessageBytes)
            {
                throw new CoreConnectionException("message_too_large", "core sent a message larger than the configured limit");
            }

            if (result.EndOfMessage)
            {
                return Encoding.UTF8.GetString(accumulator.ToArray());
            }
        }
    }

    private async Task CloseQuietlyAsync()
    {
        try
        {
            await CloseAsync(CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is WebSocketException or OperationCanceledException)
        {
            _logger.LogDebug(exception, "quiet close failed");
        }
    }
}
