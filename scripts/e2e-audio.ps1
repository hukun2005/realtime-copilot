#Requires -Version 7.0
<#
.SYNOPSIS
  音频通道端到端验证：真实 Core + 真实 C# 客户端，持续发送模拟音频帧。

.DESCRIPTION
  客户端按配置帧率发送 PCM16 帧，并用 --drop-every 主动制造序号空洞；
  脚本在客户端结束后读取 Core 的 /healthz，核对：
    · 收到的帧数 == 客户端发出的帧数
    · gaps == 主动跳过的帧数（末帧被跳过时允许少 1）
    · 无无效帧、无重复帧、无超速
    · 连接关闭后 sessions 归零

.PARAMETER Seconds
  发送时长，CI 使用 60。

.PARAMETER DropEvery
  每 N 帧跳过发送一次；0 表示不丢帧。

.EXAMPLE
  pwsh -File scripts/e2e-audio.ps1 -Seconds 60
#>
param(
    [int]$Seconds = 60,
    [int]$FramesPerSecond = 50,
    [int]$DropEvery = 500,
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-audio-e2e-" + [guid]::NewGuid().ToString("N"))
$core = $null
$client = $null

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
    $clientLog = Join-Path $dataDir "client.out"
    $clientError = Join-Path $dataDir "client.err"
    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"

    Write-Host "[audio-e2e] building audio-capture ($Configuration)"
    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Host

    $python = Get-VenvPython
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")

    $runtime = Wait-ForRuntime $runtimePath
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
    if ($health.status -ne "ok") { throw "healthz returned $($health.status)" }

    $assembly = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    $arguments = @(
        $assembly,
        "--runtime=$runtimePath",
        "--simulate-audio",
        "--seconds=$Seconds",
        "--fps=$FramesPerSecond",
        "--drop-every=$DropEvery"
    )
    Write-Host "[audio-e2e] streaming $Seconds s at $FramesPerSecond fps (drop every $DropEvery)"
    $client = Start-Process -FilePath "dotnet" -ArgumentList $arguments -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $clientLog -RedirectStandardError $clientError

    if (-not $client.WaitForExit(($Seconds + 60) * 1000)) {
        throw "client did not finish within $($Seconds + 60) seconds"
    }

    if ($client.ExitCode -ne 0) {
        $stderr = if (Test-Path $clientError) { Get-Content $clientError -Raw } else { "" }
        throw "client exited with code $($client.ExitCode)`n$stderr"
    }

    $summaryLine = Get-Content $clientLog | Where-Object { $_ -match '"simulation":' } | Select-Object -Last 1
    if (-not $summaryLine) {
        throw "client did not print a simulation summary; stdout:`n$(Get-Content $clientLog -Raw)"
    }

    $summary = ($summaryLine | ConvertFrom-Json).simulation
    $sent = [int]$summary.sent
    $skipped = [int]$summary.skipped
    Write-Host "[audio-e2e] client sent=$sent skipped=$skipped"

    $after = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
    $audio = $after.audio
    Write-Host ("[audio-e2e] core frames={0} bytes={1} gaps={2} gapEvents={3} invalid={4} duplicates={5} rateLimited={6} sessions={7}" -f `
        $audio.frames, $audio.bytes, $audio.gaps, $audio.gapEvents, $audio.invalid, $audio.duplicates, $audio.rateLimited, $audio.sessions)

    if ($sent -le 0) { throw "client reported no frames sent" }
    if ($audio.frames -ne $sent) { throw "frame count mismatch: core=$($audio.frames) client=$sent" }
    if ($audio.bytes -ne $sent * 640) { throw "byte count mismatch: core=$($audio.bytes) expected=$($sent * 640)" }
    if ($audio.invalid -ne 0) { throw "core reported $($audio.invalid) invalid frames" }
    if ($audio.duplicates -ne 0) { throw "core reported $($audio.duplicates) duplicate frames" }
    if ($audio.rateLimited -ne 0) { throw "core reported $($audio.rateLimited) rate-limited frames" }
    if ($audio.sessions -ne 0) { throw "audio session was not released (sessions=$($audio.sessions))" }

    $expectedGaps = if ($DropEvery -gt 0) { $skipped } else { 0 }
    if ($audio.gaps -ne $expectedGaps -and $audio.gaps -ne ($expectedGaps - 1)) {
        throw "gap detection mismatch: core=$($audio.gaps) expected=$expectedGaps (trailing skip tolerated)"
    }

    Write-Host "[audio-e2e] PASS"
}
finally {
    if ($client -and -not $client.HasExited) { Stop-Process -Id $client.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
