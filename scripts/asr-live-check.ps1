#Requires -Version 7.0
<#
.SYNOPSIS
  Deepgram 真实识别自检：有 API Key 时执行一次 Segment 识别，否则 SKIP。

.DESCRIPTION
  从环境变量 DEEPGRAM_API_KEY 读取密钥（不落盘、不打印）。
  输入为本地合成的 2 秒 PCM（440Hz 音 + 静音），不使用任何会议录音。
  输出 JSON：result / reason / model / events / firstPartialMs / finalMs。
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$python = $null
foreach ($candidate in @((Join-Path $root ".venv/Scripts/python.exe"), (Join-Path $root ".venv/bin/python"))) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) { throw "no .venv python found; run 'uv sync' first" }

$env:PYTHONPATH = Join-Path $root "services/copilot-core/src"
$script = Join-Path $root "scripts/asr_live_check_impl.py"
& $python $script
exit $LASTEXITCODE
