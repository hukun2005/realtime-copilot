using System.Globalization;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

// 诊断工具：通过 WASAPI 在默认输出设备播放一段正弦音，用于验证 loopback 采集链路。
// 不是产品组件，只在 scripts/capture-smoke.ps1 中使用。

var seconds = 6.0;
if (args.Length > 0 && !double.TryParse(args[0], NumberStyles.Float, CultureInfo.InvariantCulture, out seconds))
{
    Console.Error.WriteLine("usage: tone-player [seconds]");
    return 2;
}

using var enumerator = new MMDeviceEnumerator();
var device = enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);

var tone = new SignalGenerator(48000, 1)
{
    Gain = 0.3,
    Frequency = 440,
    Type = SignalGeneratorType.Sin,
};

using var player = new WasapiPlayerBuilder().WithDevice(device).Build();
player.Init(tone);
player.Play();
Console.Out.WriteLine($"playing {seconds:F1}s on {device.FriendlyName}");
Thread.Sleep(TimeSpan.FromSeconds(seconds));
player.Stop();
return 0;
