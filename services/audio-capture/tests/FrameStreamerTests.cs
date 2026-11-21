using Microsoft.Extensions.Logging.Abstractions;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class FrameStreamerTests
{
    private sealed class FakeSource : IAudioFrameSource
    {
        public string Source => "loopback";

        public bool Started { get; private set; }

        public bool Stopped { get; private set; }

        public int StopCount { get; private set; }

        public event EventHandler<AudioFrame>? FrameAvailable;

        public event EventHandler<CaptureFailure>? Failed;

        public Task StartAsync(CancellationToken cancellationToken)
        {
            Started = true;
            return Task.CompletedTask;
        }

        public Task StopAsync(CancellationToken cancellationToken)
        {
            Stopped = true;
            StopCount++;
            return Task.CompletedTask;
        }

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;

        public void Emit(AudioFrame frame) => FrameAvailable?.Invoke(this, frame);

        public void Fail(CaptureFailure failure) => Failed?.Invoke(this, failure);
    }

    private static AudioFrame Frame(uint sequence, ulong timestamp) => new(
        AudioFrameCodec.ProtocolVersion,
        AudioFrameCodec.SourceLoopback,
        AudioFrameCodec.FrameDurationMs,
        sequence,
        timestamp,
        AudioFrameCodec.SampleRate,
        AudioFrameCodec.Channels,
        AudioFrameCodec.SampleFormatPcm16,
        new byte[AudioFrameCodec.PayloadBytes]);

    [Fact]
    public async Task RunAsync_SendsEveryEmittedFrame()
    {
        var source = new FakeSource();
        var sent = new List<byte[]>();
        var streamer = new FrameStreamer(
            source,
            (payload, _) =>
            {
                sent.Add(payload.ToArray());
                return Task.CompletedTask;
            },
            NullLogger.Instance);

        using var cancellation = new CancellationTokenSource();
        var task = streamer.RunAsync(cancellation.Token);

        await WaitUntil(() => source.Started);
        for (var index = 0; index < 5; index++)
        {
            source.Emit(Frame((uint)index, 1_000_000_000ul + (ulong)index * 20_000_000));
        }

        await WaitUntil(() => streamer.FramesSent == 5);
        cancellation.Cancel();
        await task;

        Assert.Equal(5, sent.Count);
        Assert.True(source.Stopped);
        Assert.True(AudioFrameCodec.TryParse(sent[0], out var parsed, out _));
        Assert.Equal(0u, parsed.Sequence);
    }

    [Fact]
    public async Task RunAsync_PropagatesCaptureFailure()
    {
        var source = new FakeSource();
        var streamer = new FrameStreamer(source, (_, _) => Task.CompletedTask, NullLogger.Instance);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var task = streamer.RunAsync(cancellation.Token);

        await WaitUntil(() => source.Started);
        source.Fail(new CaptureFailure("device_lost", "device disappeared"));

        var exception = await Assert.ThrowsAsync<CaptureFailedException>(() => task);
        Assert.Equal("device_lost", exception.Code);
        Assert.True(source.Stopped);
    }

    [Fact]
    public async Task RunAsync_PropagatesSendFailure()
    {
        var source = new FakeSource();
        var streamer = new FrameStreamer(
            source,
            (_, _) => throw new CoreConnectionException("send_failed", "socket closed"),
            NullLogger.Instance);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var task = streamer.RunAsync(cancellation.Token);

        await WaitUntil(() => source.Started);
        source.Emit(Frame(0, 0));

        var exception = await Assert.ThrowsAsync<CoreConnectionException>(() => task);
        Assert.Equal("send_failed", exception.Code);
    }

    [Fact]
    public async Task RunAsync_DropsFramesWhenConsumerBlocks()
    {
        var source = new FakeSource();
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var streamer = new FrameStreamer(
            source,
            async (_, _) => await gate.Task.ConfigureAwait(false),
            NullLogger.Instance);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var task = streamer.RunAsync(cancellation.Token);
        await WaitUntil(() => source.Started);

        for (var index = 0; index < 300; index++)
        {
            source.Emit(Frame((uint)index, (ulong)index * 1_000_000));
        }

        await WaitUntil(() => streamer.FramesDropped > 0);
        Assert.InRange(streamer.FramesDropped, 1u, 300u);

        gate.SetResult();
        cancellation.Cancel();
        await task;
        Assert.True(source.Stopped);
    }

    [Fact]
    public async Task RunAsync_StopsSourceExactlyOnce()
    {
        var source = new FakeSource();
        var streamer = new FrameStreamer(
            source,
            (_, _) => Task.CompletedTask,
            NullLogger.Instance);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var task = streamer.RunAsync(cancellation.Token);
        await WaitUntil(() => source.Started);
        cancellation.Cancel();
        await task;

        Assert.True(source.Stopped);
        Assert.Equal(1, source.StopCount);
    }

    [Fact]
    public async Task EmitAfterStop_DoesNotThrow()
    {
        var source = new FakeSource();
        var streamer = new FrameStreamer(
            source,
            (_, _) => Task.CompletedTask,
            NullLogger.Instance);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var task = streamer.RunAsync(cancellation.Token);
        await WaitUntil(() => source.Started);
        cancellation.Cancel();
        await task;

        source.Emit(Frame(9, 9));
        Assert.Equal(1, source.StopCount);
    }

    private static async Task WaitUntil(Func<bool> condition, int timeoutMs = 3000)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (Environment.TickCount64 < deadline)
        {
            if (condition())
            {
                return;
            }

            await Task.Delay(10);
        }

        throw new TimeoutException("condition was not met");
    }
}
