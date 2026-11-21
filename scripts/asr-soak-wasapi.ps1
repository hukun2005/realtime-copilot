#Requires -Version 7.0
<#
.SYNOPSIS
  真实 WASAPI loopback + 阿里云 Paraformer 联合 soak。

.DESCRIPTION
  启动真实 Core（RTC_ASR_PROVIDER=aliyun）与 sidecar，播放本地 TTS 语音，
  通过控制面 capture.start 采集系统回放 30 分钟，周期采样 /healthz 并输出报告。
  需要系统存在可用的默认播放设备；无音频帧时报告 FAIL，不伪造。
#>
param(
    [double]$Minutes = 30,
    [int]$Interval = 30,
    [switch]$Mic
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-soak-" + [guid]::NewGuid().ToString("N"))
$core = $null
$playback = $null
$reportPath = Join-Path $root "artifacts/asr-soak/wasapi-soak.json"

function Get-VenvPython {
    foreach ($candidate in @((Join-Path $root ".venv/Scripts/python.exe"), (Join-Path $root ".venv/bin/python"))) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "no .venv python found"
}

try {
    New-Item -ItemType Directory -Force $dataDir | Out-Null
    New-Item -ItemType Directory -Force (Split-Path $reportPath) | Out-Null

    Write-Host "[soak] building audio-capture"
    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c Release --nologo | Out-Host
    $dll = Join-Path $root "services/audio-capture/bin/Release/net10.0/audio-capture.dll"

    # 生成约 30 秒中文语音，循环播放，避免依赖外部音频文件
    $wav = Join-Path $root "artifacts/soak-zh.wav"
    if (-not (Test-Path $wav)) {
        Add-Type -AssemblyName System.Speech
        $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $synth.SelectVoice("Microsoft Huihui")
        $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
            16000,
            [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
            [System.Speech.AudioFormat.AudioChannel]::Mono
        )
        $synth.SetOutputToWaveFile($wav, $format)
        for ($i = 0; $i -lt 4; $i++) {
            $synth.Speak("大家好，这是一次实时语音识别的联合测试。今天讨论 Vue3 和 TypeScript 的接口设计。系统需要持续稳定运行三十分钟。")
        }
        $synth.Dispose()
    }

    $runtimePath = Join-Path $dataDir "runtime.json"
    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $env:RTC_ASR_PROVIDER = "aliyun"
    $env:RTC_CAPTURE_SIDECAR = "dotnet $dll"

    Write-Host "[soak] starting core"
    $python = Get-VenvPython
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")

    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Path $runtimePath) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    if (-not (Test-Path $runtimePath)) { throw "core did not start" }

    Write-Host "[soak] starting playback: $wav"
    $playback = Start-Process -FilePath "pwsh" -ArgumentList @(
        "-NoProfile", "-File", (Join-Path $root "scripts/play-loop.ps1"),
        "-Wav", $wav, "-Seconds", (($Minutes + 5) * 60)
    ) -PassThru -WindowStyle Hidden

    Write-Host "[soak] running $Minutes minute(s), sampling every $Interval s"
    $extra = @()
    if ($Mic) { $extra += "--mic" }
    & $python (Join-Path $root "scripts/asr_soak_wasapi.py") `
        --runtime $runtimePath --minutes $Minutes --interval $Interval --output $reportPath @extra
    $code = $LASTEXITCODE

    if (Test-Path $reportPath) {
        Write-Host "[soak] report: $reportPath"
        Get-Content $reportPath -Raw -Encoding utf8 | ConvertFrom-Json |
            Select-Object -Property * -ExcludeProperty samples | ConvertTo-Json -Depth 6 | Out-Host
    }
    exit $code
}
finally {
    if ($playback -and -not $playback.HasExited) { Stop-Process -Id $playback.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    if ($null -ne $code -and $code -ne 0) {
        Write-Host "[soak] FAILED; core.err:"
        if (Test-Path (Join-Path $dataDir "core.err")) { Get-Content (Join-Path $dataDir "core.err") -Tail 40 | Out-Host }
        if (Test-Path (Join-Path $dataDir "core.out")) { Get-Content (Join-Path $dataDir "core.out") -Tail 40 | Out-Host }
        Write-Host "[soak] data dir kept: $dataDir"
    } else {
        Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
    }
}
