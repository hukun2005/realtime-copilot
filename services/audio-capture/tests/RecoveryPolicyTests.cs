using RealtimeCopilot.AudioCapture.Capture;
using RealtimeCopilot.AudioCapture.Control;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class RecoveryPolicyTests
{
    [Fact]
    public void DelayFor_UsesExponentialBackoff()
    {
        Assert.Equal(TimeSpan.FromMilliseconds(500), RecoveryPolicy.DelayFor(1));
        Assert.Equal(TimeSpan.FromSeconds(1), RecoveryPolicy.DelayFor(2));
        Assert.Equal(TimeSpan.FromSeconds(2), RecoveryPolicy.DelayFor(3));
    }

    [Fact]
    public void DelayFor_ClampsOutOfRangeAttempts()
    {
        Assert.Equal(TimeSpan.FromMilliseconds(500), RecoveryPolicy.DelayFor(0));
        Assert.Equal(TimeSpan.FromSeconds(2), RecoveryPolicy.DelayFor(9));
    }

    [Fact]
    public void Probe_ReportsNotVerifiedForRealTargets()
    {
        var report = ProcessLoopbackProbe.Probe(null);

        Assert.Equal(20348, ProcessLoopbackProbe.MinimumBuild);
        Assert.Equal("NOT_VERIFIED", report.TeamsMeeting);
        Assert.Equal("NOT_VERIFIED", report.WeChatPc);
        Assert.Equal("NOT_VERIFIED", report.RealAudio);
        Assert.Null(report.TargetPidValid);
    }

    [Fact]
    public void Probe_RejectsUnknownPid()
    {
        var report = ProcessLoopbackProbe.Probe(int.MaxValue);

        Assert.False(report.TargetPidValid);
    }

    [Fact]
    public void Probe_AcceptsCurrentProcessPid()
    {
        var report = ProcessLoopbackProbe.Probe(Environment.ProcessId);

        Assert.True(report.TargetPidValid);
        Assert.Equal(Environment.OSVersion.Version.Build, report.WindowsBuild);
    }
}
