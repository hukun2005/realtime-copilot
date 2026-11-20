#Requires -Version 7.0
<#
.SYNOPSIS
  控制面端到端：真实 Core + 真实 C# sidecar + 真实 capture.start / capture.stop。

.DESCRIPTION
  验证完整链路：UI 角色发命令 → Core 状态机 → 拉起 sidecar → WASAPI 采集 → audio.status 回传。
  结束后检查 sidecar 已退出、状态回到 idle、没有残留进程。

.EXAMPLE
  pwsh -File scripts/e2e-control.ps1 -Seconds 8
#>
param(
    [int]$Seconds = 8,
    [string]$Configuration = "Release",
    [switch]$Simulated
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-control-" + [guid]::NewGuid().ToString("N"))
$core = $null

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
    $python = Get-VenvPython

    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Host
    & dotnet build (Join-Path $root "tools/tone-player/TonePlayer.csproj") -c $Configuration --nologo | Out-Null
    $tonePlayer = Join-Path $root "tools/tone-player/bin/$Configuration/net10.0/tone-player.dll"
    $captureDll = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"

    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $env:RTC_CAPTURE_SIDECAR = "dotnet $captureDll"
    if ($Simulated) {
        # 无音频端点的 CI：sidecar 用静音帧源，仍然走真实的控制与二进制帧链路。
        $env:RTC_CAPTURE_SIMULATED = "1"
    }
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")
    $runtime = Wait-ForRuntime $runtimePath
    $httpBase = "http://127.0.0.1:$($runtime.port)"
    Write-Host "[control-e2e] core ready on $($runtime.port)"

    if (-not $Simulated) {
        $player = Start-Process -FilePath "dotnet" -ArgumentList $tonePlayer, "6" -PassThru -WindowStyle Hidden
    }

    $sessionOut = & $python (Join-Path $root "scripts/control_client.py") session --runtime $runtimePath --seconds $Seconds 2>&1 | Select-Object -Last 1
    $session = $sessionOut | ConvertFrom-Json
    Write-Host "[control-e2e] session: $sessionOut"

    if (-not $session.ok) { throw "control session failed: $sessionOut" }
    if ($session.start.state -ne "capturing") { throw "capture did not reach capturing" }
    if ($session.stop.state -ne "idle") { throw "capture did not return to idle" }
    if ($session.health.audio.frames -lt 50) { throw "too few frames: $($session.health.audio.frames)" }

    $health = Invoke-RestMethod -Uri "$httpBase/healthz" -TimeoutSec 5
    if ($health.capture.state -ne "idle") { throw "capture state is $($health.capture.state), expected idle" }
    if ($health.capture.sidecarPid) { throw "sidecar still running: $($health.capture.sidecarPid)" }

    $leftover = Get-Process -Name "audio-capture" -ErrorAction SilentlyContinue
    if ($leftover) { throw "audio-capture process leaked" }

    if ($player -and -not $player.HasExited) { Stop-Process -Id $player.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "[control-e2e] PASS frames=$($session.health.audio.frames) peak=$($session.health.audio.peakDbfs)dBFS simulated=$Simulated"
}
finally {
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
