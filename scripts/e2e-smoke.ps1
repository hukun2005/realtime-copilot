#Requires -Version 7.0
<#
.SYNOPSIS
  跨语言端到端冒烟：启动真实 Python Core，再用真实 C# 客户端连接并握手。

.DESCRIPTION
  不做 mock：Core 用 uvicorn 监听回环端口，客户端用 ClientWebSocket 走真实 TCP。
  断言客户端日志出现 "core ready"，并检查 /healthz 的协议版本与角色计数。

.PARAMETER Seconds
  客户端连接后保持运行的秒数，用于观察心跳。

.EXAMPLE
  pwsh -File scripts/e2e-smoke.ps1 -Seconds 5
#>
param(
    [int]$Seconds = 5,
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-e2e-" + [guid]::NewGuid().ToString("N"))
$core = $null
$client = $null

function Get-VenvPython {
    $candidates = @(
        (Join-Path $root ".venv/Scripts/python.exe"),
        (Join-Path $root ".venv/bin/python")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "no .venv python found; run 'uv sync' first"
}

function Wait-ForRuntime([string]$path, [int]$timeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $path) {
            try {
                return Get-Content $path -Raw | ConvertFrom-Json
            } catch {
                Start-Sleep -Milliseconds 200
                continue
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "core did not write $path within $timeoutSeconds seconds"
}

try {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    $runtimePath = Join-Path $dataDir "runtime.json"
    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"

    Write-Host "[e2e] building audio-capture ($Configuration)"
    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Host

    $python = Get-VenvPython
    Write-Host "[e2e] starting core: $python -m copilot_core"
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")

    $runtime = Wait-ForRuntime $runtimePath
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
    if ($health.status -ne "ok") { throw "healthz returned status $($health.status)" }
    if ($health.protocol -ne 1) { throw "unexpected protocol $($health.protocol)" }
    Write-Host "[e2e] core healthy on port $($runtime.port) version=$($health.version)"

    $clientLog = Join-Path $dataDir "client.out"
    $clientError = Join-Path $dataDir "client.err"
    $assembly = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    Write-Host "[e2e] starting client: $assembly"
    $client = Start-Process -FilePath "dotnet" -ArgumentList $assembly, "--runtime=$runtimePath" `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $clientLog -RedirectStandardError $clientError

    Start-Sleep -Seconds $Seconds

    $log = if (Test-Path $clientLog) { Get-Content $clientLog -Raw } else { "" }
    if ($log -notmatch "core ready: server=") {
        $stderr = if (Test-Path $clientError) { Get-Content $clientError -Raw } else { "" }
        throw "client did not reach system.ready`nstdout:`n$log`nstderr:`n$stderr"
    }

    $after = $null
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        $candidate = Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5
        if ($candidate.roles.audio -eq 1) { $after = $candidate; break }
        Start-Sleep -Milliseconds 200
    }

    if ($null -eq $after) {
        $stderr = if (Test-Path $clientError) { Get-Content $clientError -Raw } else { "" }
        $stdout = if (Test-Path $clientLog) { Get-Content $clientLog -Raw } else { "" }
        $healthJson = (Invoke-RestMethod -Uri "http://127.0.0.1:$($runtime.port)/healthz" -TimeoutSec 5) | ConvertTo-Json -Compress
        $exit = if ($client.HasExited) { $client.ExitCode } else { "running" }
        throw "expected 1 audio connection`nhealth: $healthJson`nclient exit: $exit`nstdout:`n$stdout`nstderr:`n$stderr"
    }

    Write-Host "[e2e] client connected; roles=$($after.roles | ConvertTo-Json -Compress)"
    Write-Host "[e2e] PASS"
}
finally {
    if ($client -and -not $client.HasExited) { Stop-Process -Id $client.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
