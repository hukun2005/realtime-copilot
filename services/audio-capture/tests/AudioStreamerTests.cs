using Microsoft.Extensions.Logging.Abstractions;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class AudioStreamerTests
{
    private static AudioStreamer CreateStreamer(
        Func<ReadOnlyMemory<byte>, CancellationToken, Task> send,
        int dropEvery = 0,
        uint startSequence = 0)
    {
        var tick = 0UL;
        return new AudioStreamer(
            send,
            new SimulatedAudioSource(startSequence: startSequence),
            NullLogger.Instance,
            framesPerSecond: 200,
            dropEvery: dropEvery,
            timestamp: () => 1_000_000_000 + (tick += 20_000_000));
    }

    [Fact]
    public async Task RunAsync_SendsFramesWithIncreasingSequence()
    {
        var sequences = new List<uint>();
        var streamer = CreateStreamer((payload, _) =>
        {
            Assert.True(AudioFrameCodec.TryParse(payload.Span, out var frame, out var error), error);
            sequences.Add(frame.Sequence);
            return Task.CompletedTask;
        });

        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(150));
        await streamer.RunAsync(cancellation.Token);

        Assert.NotEmpty(sequences);
        Assert.Equal(streamer.FramesSent, (uint)sequences.Count);
        for (var index = 1; index < sequences.Count; index++)
        {
            Assert.Equal(sequences[index - 1] + 1, sequences[index]);
        }
    }

    [Fact]
    public async Task RunAsync_SkipsSequenceNumbersWhenDropInjectionEnabled()
    {
        var sequences = new List<uint>();
        var streamer = CreateStreamer(
            (payload, token) =>
            {
                AudioFrameCodec.TryParse(payload.Span, out var frame, out var parseError);
                Assert.True(string.IsNullOrEmpty(parseError), parseError);
                sequences.Add(frame.Sequence);
                return Task.CompletedTask;
            },
            dropEvery: 3);

        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(150));
        await streamer.RunAsync(cancellation.Token);

        Assert.NotEmpty(sequences);
        Assert.True(streamer.FramesSkipped > 0);
        // 每 3 帧跳过一次：被跳过的序号对 3 取余为 2。
        Assert.DoesNotContain(sequences, sequence => sequence % 3 == 2);
    }

    [Fact]
    public async Task RunAsync_StopsImmediatelyWhenAlreadyCancelled()
    {
        var sent = 0;
        var streamer = CreateStreamer((_, _) =>
        {
            sent++;
            return Task.CompletedTask;
        });

        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await streamer.RunAsync(cancellation.Token);

        Assert.Equal(0, sent);
        Assert.Equal(0u, streamer.FramesSent);
    }

    [Fact]
    public async Task RunAsync_PropagatesSendFailure()
    {
        var streamer = CreateStreamer((_, _) =>
            throw new CoreConnectionException("send_failed", "socket closed"));

        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var exception = await Assert.ThrowsAsync<CoreConnectionException>(
            () => streamer.RunAsync(cancellation.Token));
        Assert.Equal("send_failed", exception.Code);
    }

    [Fact]
    public async Task RunAsync_StopsWhenCancelledMidStream()
    {
        var streamer = CreateStreamer((_, _) => Task.CompletedTask);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(80));

        await streamer.RunAsync(cancellation.Token);

        Assert.True(streamer.FramesSent > 0);
        Assert.Contains("\"sent\":", streamer.ToJson(), StringComparison.Ordinal);
    }
}
