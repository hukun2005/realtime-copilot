#Requires -Version 7.0
<#
.SYNOPSIS
  采集诊断报告：输出机器可读 JSON（Windows 版本、设备、采集参数与实测统计）。

.DESCRIPTION
  通过控制面启动一次短采集，采样 /healthz，结束后输出一份 JSON 报告。
  报告不含 PCM、会议内容或任何密钥。

.EXAMPLE
  pwsh -File tools/capture-probe.ps1 -Seconds 10
#>
param(
    [int]$Seconds = 10,
    [string]$Configuration = "Release",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $root ".artifacts/probe" }
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-probe-" + [guid]::NewGuid().ToString("N"))
$core = $null
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
    New-Item -ItemType Directory -Path $dataDir, $OutDir -Force | Out-Null
    $python = Get-VenvPython
    $runtimePath = Join-Path $dataDir "runtime.json"

    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Null
    & dotnet build (Join-Path $root "tools/tone-player/TonePlayer.csproj") -c $Configuration --nologo | Out-Null
    $tonePlayer = Join-Path $root "tools/tone-player/bin/$Configuration/net10.0/tone-player.dll"
    $captureDll = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"

    $devices = & dotnet $captureDll --list-devices | ConvertFrom-Json
    $os = Get-CimInstance Win32_OperatingSystem

    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $env:RTC_CAPTURE_SIDECAR = "dotnet $captureDll"
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")
    $runtime = Wait-ForRuntime $runtimePath
    $httpBase = "http://127.0.0.1:$($runtime.port)"

    $startedAt = Get-Date
    $startOut = & $python (Join-Path $root "scripts/control_client.py") start --runtime $runtimePath 2>&1 | Select-Object -Last 1
    $player = Start-Process -FilePath "dotnet" -ArgumentList $tonePlayer, "4" -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds $Seconds
    $during = Invoke-RestMethod -Uri "$httpBase/healthz" -TimeoutSec 5
    $stopOut = & $python (Join-Path $root "scripts/control_client.py") stop --runtime $runtimePath 2>&1 | Select-Object -Last 1
    $after = Invoke-RestMethod -Uri "$httpBase/healthz" -TimeoutSec 5

    $report = [ordered]@{
        generatedAt = (Get-Date).ToString("o")
        windows = [ordered]@{
            caption = $os.Caption
            version = $os.Version
            build = $os.BuildNumber
        }
        devices = $devices
        capture = [ordered]@{
            source = "loopback"
            sampleRate = 16000
            channels = 1
            frameMs = 20
            frameBytes = 668
            state = $after.capture.state
            simulated = $false
            sidecarExitCode = $after.capture.sidecarExitCode
            sidecarTermination = $after.capture.sidecarTermination
            startState = ($startOut | ConvertFrom-Json).start.state
            stopState = ($stopOut | ConvertFrom-Json).stop.state
            startOutput = $startOut
            stopOutput = $stopOut
        }
        runtime = [ordered]@{
            durationSeconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
            requestedSeconds = $Seconds
        }
        stats = [ordered]@{
            frames = $during.audio.frames
            bytes = $during.audio.bytes
            gaps = $during.audio.gaps
            duplicates = $during.audio.duplicates
            invalid = $during.audio.invalid
            rateLimited = $during.audio.rateLimited
            peakDbfs = $during.audio.peakDbfs
            lastRmsDbfs = $during.audio.lastRmsDbfs
            silenceDurationMs = $null
            lastFrameTimestamp = $during.audio.lastFrameTsNs
        }
        errors = [ordered]@{
            errorCode = $during.capture.errorCode
            error = $during.capture.error
        }
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $path = Join-Path $OutDir "capture-probe-$stamp.json"
    $report | ConvertTo-Json -Depth 6 | Set-Content $path -Encoding utf8
    Write-Host "[probe] report: $path"
    $report | ConvertTo-Json -Depth 4 -Compress
}
finally {
    if ($player -and -not $player.HasExited) { Stop-Process -Id $player.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
