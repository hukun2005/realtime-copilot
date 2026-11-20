using RealtimeCopilot.AudioCapture;
using RealtimeCopilot.AudioCapture.Audio;

namespace RealtimeCopilot.AudioCapture.Tests;

public sealed class SimulationOptionsTests
{
    [Fact]
    public void Parse_DefaultsToDisabled()
    {
        var options = SimulationOptions.Parse([]);

        Assert.False(options.Enabled);
        Assert.Equal(TimeSpan.Zero, options.Duration);
        Assert.Equal(50, options.FramesPerSecond);
        Assert.Equal(0, options.DropEvery);
    }

    [Fact]
    public void Parse_ReadsAllFlags()
    {
        var options = SimulationOptions.Parse(["--simulate-audio", "--seconds=60", "--fps=25", "--drop-every=500"]);

        Assert.True(options.Enabled);
        Assert.Equal(TimeSpan.FromSeconds(60), options.Duration);
        Assert.Equal(25, options.FramesPerSecond);
        Assert.Equal(500, options.DropEvery);
    }

    [Theory]
    [InlineData("--fps=0")]
    [InlineData("--fps=500")]
    [InlineData("--seconds=-1")]
    [InlineData("--seconds=9000")]
    [InlineData("--drop-every=-5")]
    [InlineData("--fps=abc")]
    public void Parse_RejectsOutOfRangeValues(string argument)
    {
        Assert.Throws<ConfigurationException>(() => SimulationOptions.Parse([argument]));
    }

    [Fact]
    public void Parse_IgnoresConnectionArguments()
    {
        var options = SimulationOptions.Parse(["--ws-url=ws://127.0.0.1:1/ws", "--token=abc"]);

        Assert.False(options.Enabled);
    }
}
