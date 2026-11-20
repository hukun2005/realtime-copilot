#Requires -Version 7.0
<#
.SYNOPSIS
  真实 WASAPI loopback 采集冒烟：播放测试音，用 CLI 采集进程抓取，再核对 Core 统计与电平。

.DESCRIPTION
  结果是三态之一，并以退出码表达：

    PASS（0）  采集到足够帧且整段峰值 > -45 dBFS
    SKIP（0）  环境明确不具备测试条件（没有可用的 loopback 端点）
    FAIL（1）  采集进程异常、没有帧、有无效帧、或采到静音

  设备存在但采集失败一定是 FAIL，不会被当成 SKIP。错误码：
    no_audio_device / playback_failed / capture_failed / no_frames / invalid_frames / silent_capture

.EXAMPLE
  pwsh -File scripts/capture-smoke.ps1 -Seconds 8
#>
param(
    [int]$Seconds = 8,
    [string]$Configuration = "Release",
    [switch]$SkipPlayback
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-capture-" + [guid]::NewGuid().ToString("N"))
$core = $null
$client = $null
$player = $null

function Get-VenvPython {
    foreach ($candidate in @((Join-Path $root ".venv/Scripts/python.exe"), (Join-Path $root ".venv/bin/python"))) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "no .venv python found; run 'uv sync' first"
}

function Wait-ForRuntime([string]$path, [int]$timeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $path) {
            try { return Get-Content $path -Raw | ConvertFrom-Json } catch { Start-Sleep -Milliseconds 200; continue }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "core did not write $path within $timeoutSeconds seconds"
}

function Finish([string]$Result, [string]$Code, [string]$Detail, [int]$ExitCode) {
    $payload = [ordered]@{ result = $Result; code = $Code; detail = $Detail } | ConvertTo-Json -Compress
    Write-Host "[capture-smoke] $Result ($Code): $Detail"
    Write-Host "[capture-smoke] $payload"
    exit $ExitCode
}

try {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    $python = Get-VenvPython
    $runtimePath = Join-Path $dataDir "runtime.json"
    $wavPath = Join-Path $dataDir "tone.wav"

    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Null
    & dotnet build (Join-Path $root "tools/tone-player/TonePlayer.csproj") -c $Configuration --nologo | Out-Null
    $captureDll = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    $tonePlayer = Join-Path $root "tools/tone-player/bin/$Configuration/net10.0/tone-player.dll"

    $devices = & dotnet $captureDll --list-devices | ConvertFrom-Json
    $loopback = $devices | Where-Object { $_.flow -eq "loopback" } | Select-Object -First 1
    if (-not $loopback) {
        Finish "SKIP" "no_audio_device" "no loopback endpoint is available on this host" 0
    }

    $toneScript = @"
import math, struct, sys, wave
with wave.open(sys.argv[1], 'wb') as handle:
    handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
    handle.writeframes(b''.join(
        struct.pack('<h', int(12000 * math.sin(2 * math.pi * 440 * index / 16000)))
        for index in range(16000 * 6)
    ))
"@
    & $python -c $toneScript $wavPath

    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")
    $runtime = Wait-ForRuntime $runtimePath

    $clientLog = Join-Path $dataDir "client.out"
    $clientErr = Join-Path $dataDir "client.err"
    $client = Start-Process -FilePath "dotnet" `
        -ArgumentList $captureDll, "--runtime=$runtimePath", "--capture-loopback", "--seconds=$Seconds" `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $clientLog -RedirectStandardError $clientErr

    Start-Sleep -Milliseconds 500
    if (-not $SkipPlayback) {
        $player = Start-Process -FilePath "dotnet" -ArgumentList $tonePlayer, "5" -PassThru -WindowStyle Hidden
    }

    if (-not $client.WaitForExit(($Seconds + 40) * 1000)) {
        Finish "FAIL" "capture_failed" "capture client did not finish in time" 1
    }

    if ($player) {
        $player.WaitForExit(15000) | Out-Null
        if ($player.ExitCode -ne 0) {
            Finish "FAIL" "playback_failed" "tone player exited with code $($player.ExitCode)" 1
        }
    }

    if ($client.ExitCode -ne 0) {
        $stderr = if (Test-Path $clientErr) { (Get-Content $clientErr -Raw).Trim() } else { "" }
        Finish "FAIL" "capture_failed" "capture exited $($client.ExitCode): $stderr" 1
    }

    $summaryLine = Get-Content $clientLog | Where-Object { $_ -match '"capture":' } | Select-Object -Last 1
    if (-not $summaryLine) {
        Finish "FAIL" "capture_failed" "capture printed no summary" 1
    }

    $summary = ($summaryLine | ConvertFrom-Json).capture
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
    $audio = $health.audio

    $detail = "sent=$($summary.sent) frames=$($audio.frames) peak=$($audio.peakDbfs)dBFS rms=$($audio.lastRmsDbfs)dBFS gaps=$($audio.gaps) invalid=$($audio.invalid)"

    . (Join-Path $PSScriptRoot "capture-verdict.ps1")
    $verdict = Get-CaptureVerdict -DevicePresent $true -PlaybackFailed $false -CaptureExited $false `
        -Frames $audio.frames -Invalid $audio.invalid -PeakDbfs $audio.peakDbfs
    Finish $verdict.Result $verdict.Code $detail $verdict.ExitCode
}
catch {
    Finish "FAIL" "unexpected" $_.Exception.Message 1
}
finally {
    foreach ($process in @($player, $client, $core)) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
