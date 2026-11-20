using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class CaptureOptionsTests
{
    [Fact]
    public void Parse_DefaultsToDisabled()
    {
        var options = CaptureOptions.Parse([]);

        Assert.False(options.Enabled);
        Assert.False(options.Loopback);
        Assert.False(options.Mic);
    }

    [Fact]
    public void Parse_ReadsLoopbackFlag()
    {
        var options = CaptureOptions.Parse(["--capture-loopback"]);

        Assert.True(options.Enabled);
        Assert.True(options.Loopback);
        Assert.False(options.Mic);
    }

    [Fact]
    public void Parse_ReadsMicrophoneFlagAndDeviceIds()
    {
        var options = CaptureOptions.Parse(
            ["--capture-mic", "--capture-device={render}", "--mic-device={capture}"]);

        Assert.True(options.Mic);
        Assert.False(options.Loopback);
        Assert.Equal("{render}", options.LoopbackDeviceId);
        Assert.Equal("{capture}", options.MicDeviceId);
    }

    [Fact]
    public void Parse_IgnoresUnrelatedArguments()
    {
        var options = CaptureOptions.Parse(["--simulate-audio", "--seconds=10"]);

        Assert.False(options.Enabled);
    }
}
