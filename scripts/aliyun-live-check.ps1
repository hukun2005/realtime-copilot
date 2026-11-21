#Requires -Version 7.0
<#
.SYNOPSIS
  阿里云百炼 Paraformer 真实识别自检。

.DESCRIPTION
  需要 DASHSCOPE_API_KEY 与 DASHSCOPE_WORKSPACE_ID。
  若 artifacts/tts-zh.wav 不存在，会用 Windows 中文 TTS 生成一段测试语音（非会议录音）。
  输出 JSON：result / connectMs / partials / finals / results / metrics。
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wav = Join-Path $root "artifacts/tts-zh.wav"

if (-not (Test-Path $wav)) {
    New-Item -ItemType Directory -Force (Split-Path $wav) | Out-Null
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SelectVoice("Microsoft Huihui")
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $synth.SetOutputToWaveFile($wav, $format)
    $synth.Speak("大家好，这是一次实时语音识别的测试。今天讨论 Vue3 和 TypeScript 的接口设计。")
    $synth.Dispose()
}

$python = $null
foreach ($candidate in @((Join-Path $root ".venv/Scripts/python.exe"), (Join-Path $root ".venv/bin/python"))) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) { throw "no .venv python found; run 'uv sync' first" }

$env:PYTHONPATH = Join-Path $root "services/copilot-core/src"
$env:PYTHONIOENCODING = "utf-8"
& $python (Join-Path $root "scripts/aliyun_live_check_impl.py") --wav $wav
exit $LASTEXITCODE
