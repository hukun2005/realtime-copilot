# 生成 P3.5 合成评测音频（Windows System.Speech，16k mono PCM16）。
# 真实的会议录音不进入仓库；本脚本只生成可复现的 TTS 基线。
param(
    [string]$OutputDirectory = "evaluation/dataset/audio",
    [string]$VoiceName = "Microsoft Huihui"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.IO

$root = Split-Path -Parent $PSScriptRoot
$output = Join-Path $root $OutputDirectory
New-Item -ItemType Directory -Force $output | Out-Null

$samples = @(
    @{ Id = "clean_01"; Text = "大家好，今天我们讨论实时转写和会议助手的架构设计。" },
    @{ Id = "clean_02"; Text = "请把接口文档发到群里，下午三点前确认。" },
    @{ Id = "technical_01"; Text = "我们用 Vue3 和 TypeScript 实现桌面端，后端是 Python 和 SQLite。" },
    @{ Id = "numbers_01"; Text = "第一季度营收是一千二百三十四万元，同比增长百分之十五。" },
    @{ Id = "mixed_01"; Text = "这个 API 的 P95 延迟要控制在八百毫秒以内。" },
    @{ Id = "filler_01"; Text = "嗯，那个，我们再确认一下这个方案，好吧？" },
    @{ Id = "long_01"; Text = "今天的会议主要讨论三个议题。第一是实时转写的准确率，第二是端到端延迟，第三是会议结束后的记录整理。请各位分别说明自己负责的部分，并在周五之前给出结论。" }
)

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice($VoiceName)
$synth.Rate = 0
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

foreach ($sample in $samples) {
    $path = Join-Path $output ("{0}.wav" -f $sample.Id)
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($sample.Text)
    $synth.SetOutputToNull()
    Write-Host ("generated {0}" -f $path)
}
$synth.Dispose()
