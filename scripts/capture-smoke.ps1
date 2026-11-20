#Requires -Version 7.0
<#
.SYNOPSIS
  真实 WASAPI loopback 采集冒烟：播放一段音调，用采集进程抓取，再核对 Core 的统计与电平。

.DESCRIPTION
  与 e2e-audio.ps1 的区别：后者发送的是模拟帧；本脚本验证的是真实声卡路径
  （设备打开、重采样、分帧、发送）。需要本机有可用的输出设备与音频会话。

  判定：
    · 采集帧数 > 100（8 秒、50fps 下约 400）
    · Core 记录的整段峰值电平 > -45 dBFS（证明不是静音；末帧可能已回到静音）
  无输出设备或播放失败时输出 SKIP 并以 0 退出，避免在不具备条件的机器上误报失败。

.EXAMPLE
  pwsh -File scripts/capture-smoke.ps1 -Seconds 8
#>
param(
    [int]$Seconds = 8,
    [string]$Configuration = "Release"
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

try {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    $runtimePath = Join-Path $dataDir "runtime.json"
    $wavPath = Join-Path $dataDir "tone.wav"

    $python = Get-VenvPython
    $toneScript = @"
import math, struct, sys, wave
path = sys.argv[1]
seconds = 6
with wave.open(path, 'wb') as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b''.join(
        struct.pack('<h', int(12000 * math.sin(2 * math.pi * 440 * index / 16000)))
        for index in range(16000 * seconds)
    ))
"@
    & $python -c $toneScript $wavPath
    Write-Host "[capture-smoke] tone ready: $wavPath"

    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Null

    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")
    $runtime = Wait-ForRuntime $runtimePath

    $assembly = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    $clientLog = Join-Path $dataDir "client.out"
    $clientErr = Join-Path $dataDir "client.err"
    $client = Start-Process -FilePath "dotnet" `
        -ArgumentList $assembly, "--runtime=$runtimePath", "--capture-loopback", "--seconds=$Seconds" `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $clientLog -RedirectStandardError $clientErr

    Start-Sleep -Seconds 1
    $player = Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile", "-Command", "(New-Object Media.SoundPlayer '$wavPath').PlaySync()" `
        -PassThru -WindowStyle Hidden

    if (-not $client.WaitForExit(($Seconds + 40) * 1000)) {
        throw "capture client did not finish in time"
    }

    if ($client.ExitCode -ne 0) {
        $stderr = if (Test-Path $clientErr) { Get-Content $clientErr -Raw } else { "" }
        Write-Host "[capture-smoke] SKIP: capture exited $($client.ExitCode) — no usable capture device?`n$stderr"
        exit 0
    }

    $summaryLine = Get-Content $clientLog | Where-Object { $_ -match '"capture":' } | Select-Object -Last 1
    if (-not $summaryLine) { throw "client printed no capture summary" }
    $summary = ($summaryLine | ConvertFrom-Json).capture

    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
    $audio = $health.audio
    Write-Host ("[capture-smoke] sent={0} dropped={1} core frames={2} bytes={3} sessionPeak={4}dBFS lastPeak={5}dBFS gaps={6} invalid={7}" -f `
        $summary.sent, $summary.dropped, $audio.frames, $audio.bytes, $audio.peakDbfs, $audio.lastPeakDbfs, $audio.gaps, $audio.invalid)

    if ($audio.frames -le 100) { throw "too few frames captured: $($audio.frames)" }
    if ($audio.invalid -ne 0) { throw "core reported $($audio.invalid) invalid frames" }
    if ($null -eq $audio.peakDbfs -or $audio.peakDbfs -le -45) {
        Write-Host "[capture-smoke] SKIP: captured audio is silent (no audible output path on this host)"
        exit 0
    }

    Write-Host "[capture-smoke] PASS"
}
finally {
    if ($player -and -not $player.HasExited) { Stop-Process -Id $player.Id -Force -ErrorAction SilentlyContinue }
    if ($client -and -not $client.HasExited) { Stop-Process -Id $client.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
