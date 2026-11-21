#Requires -Version 7.0
<#
.SYNOPSIS
  采集稳定性测试：真实 Core + 真实 C# sidecar，按固定间隔采样资源与帧统计。

.DESCRIPTION
  通过控制面启动采集（capture.start），持续播放测试音，采样：
  采集进程 CPU / WorkingSet / PrivateBytes、Core 的帧率 / 丢帧 / 错误 / 电平、采集状态。
  结束后写出 JSON 与 CSV 报告。

  本脚本只产出可复现报告，不预设"零丢帧"目标。

.EXAMPLE
  pwsh -File scripts/capture-stability.ps1 -Minutes 30
#>
param(
    [int]$Minutes = 30,
    [int]$SampleSeconds = 5,
    [string]$Configuration = "Release",
    [string]$OutDir = "",
    [switch]$Simulated
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $root "artifacts/capture-stability" }
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rtc-stability-" + [guid]::NewGuid().ToString("N"))
$core = $null
$session = $null
$toneLoop = $null

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
    $runtimePath = Join-Path $dataDir "runtime.json"
    $wavPath = Join-Path $dataDir "tone.wav"
    $python = Get-VenvPython

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

    & dotnet build (Join-Path $root "tools/tone-player/TonePlayer.csproj") -c $Configuration --nologo | Out-Null
    & dotnet build (Join-Path $root "services/audio-capture/AudioCapture.csproj") -c $Configuration --nologo | Out-Null
    $tonePlayer = Join-Path $root "tools/tone-player/bin/$Configuration/net10.0/tone-player.dll"
    $captureDll = Join-Path $root "services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    $gitSha = (git -C $root rev-parse HEAD 2>$null)
    if (-not $gitSha) { $gitSha = "unknown" }
    $sidecarVersion = (dotnet $captureDll --version 2>$null)
    if (-not $sidecarVersion) { $sidecarVersion = "unknown" }
    $mode = if ($Simulated) { "simulated" } else { "wasapi-loopback" }

    if (-not $Simulated) {
        $devices = & dotnet $captureDll --list-devices | ConvertFrom-Json
        if (-not ($devices | Where-Object { $_.flow -eq "loopback" })) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $skip = [pscustomobject]@{
                result = "SKIP"; reason = "no_audio_device"; simulated = $false
                gitSha = $gitSha; sidecarVersion = $sidecarVersion
                generatedAt = (Get-Date).ToString("o")
            }
            $skipPath = Join-Path $OutDir "stability-$stamp-skip.json"
            $skip | ConvertTo-Json -Depth 3 | Set-Content $skipPath -Encoding utf8
            Write-Host "[stability] SKIP (no audio device): $skipPath"
            $skip | ConvertTo-Json -Compress
            exit 0
        }
    }

    $env:RTC_DATA_DIR = $dataDir
    $env:RTC_SERVER_PORT = "0"
    $env:RTC_CAPTURE_SIDECAR = "dotnet services/audio-capture/bin/$Configuration/net10.0/audio-capture.dll"
    if ($Simulated) { $env:RTC_CAPTURE_SIMULATED = "1" }
    $core = Start-Process -FilePath $python -ArgumentList "-m", "copilot_core" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dataDir "core.out") `
        -RedirectStandardError (Join-Path $dataDir "core.err")
    $runtime = Wait-ForRuntime $runtimePath
    $httpBase = "http://127.0.0.1:$($runtime.port)"

    # 真实设备模式持续播放测试音；模拟模式不需要。
    if (-not $Simulated) {
        $toneLoop = Start-Process -FilePath "pwsh" -ArgumentList "-NoProfile", "-Command",
            "`$deadline = (Get-Date).AddMinutes($Minutes); while ((Get-Date) -lt `$deadline) { dotnet '$tonePlayer' 5 | Out-Null }" `
            -PassThru -WindowStyle Hidden
    }

    $startOut = & $python (Join-Path $root "scripts/control_client.py") start --runtime $runtimePath 2>&1 | Select-Object -Last 1
    Write-Host "[stability] start: $startOut"
    if ($startOut -notmatch '"state": "capturing"') { throw "capture did not start: $startOut" }
    $captureStartedAt = Get-Date
    $captureStartedAtText = $captureStartedAt.ToString("o")

    Start-Sleep -Seconds 2
    $samples = [System.Collections.Generic.List[object]]::new()
    $transitions = [System.Collections.Generic.List[object]]::new()
    $startedAt = Get-Date
    $lastFrames = 0
    $lastCaptureState = $null

    while (((Get-Date) - $startedAt).TotalMinutes -lt $Minutes) {
        $health = Invoke-RestMethod -Uri "$httpBase/healthz" -TimeoutSec 5
        $sidecarPid = $health.capture.sidecarPid
        $cpuSeconds = $null; $workingSet = $null; $privateBytes = $null
        if ($sidecarPid) {
            $process = Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue
            if ($process) {
                $process.Refresh()
                $cpuSeconds = [Math]::Round($process.TotalProcessorTime.TotalSeconds, 2)
                $workingSet = $process.WorkingSet64
                $privateBytes = $process.PrivateMemorySize64
            }
        }

        $frames = [int]$health.audio.frames
        $samples.Add([pscustomobject]@{
            at = (Get-Date).ToString("o")
            elapsedSeconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
            captureState = $health.capture.state
            cpuSeconds = $cpuSeconds
            workingSetBytes = $workingSet
            privateBytes = $privateBytes
            frames = $frames
            framesDelta = $frames - $lastFrames
            gaps = [int]$health.audio.gaps
            invalid = [int]$health.audio.invalid
            duplicates = [int]$health.audio.duplicates
            rateLimited = [int]$health.audio.rateLimited
            peakDbfs = $health.audio.peakDbfs
            sessions = [int]$health.audio.sessions
        })
        if ($lastCaptureState -ne $health.capture.state) {
            $transitions.Add([pscustomobject]@{ at = (Get-Date).ToString("o"); from = $lastCaptureState; to = $health.capture.state })
            $lastCaptureState = $health.capture.state
        }
        $lastFrames = $frames
        Start-Sleep -Seconds $SampleSeconds
    }

    $captureEndedAt = Get-Date

    # 主动停止采集，并等待状态回到 idle。
    $stopOut = & $python (Join-Path $root "scripts/control_client.py") stop --runtime $runtimePath 2>&1 | Select-Object -Last 1
    Write-Host "[stability] stop: $stopOut"
    $finalHealth = Invoke-RestMethod -Uri "$httpBase/healthz" -TimeoutSec 5

    $last = $samples | Select-Object -Last 1
    $first = $samples | Select-Object -First 1
    $windows = Get-CimInstance Win32_OperatingSystem
    $elapsedMinutes = ((Get-Date) - $startedAt).TotalMinutes
    $elapsedSeconds = ($captureEndedAt - $captureStartedAt).TotalSeconds
    $theoreticalFrames = [Math]::Round($elapsedSeconds * 50)
    $expectedSamples = [Math]::Floor($Minutes * 60 / $SampleSeconds)
    $sampleGaps = @()
    for ($index = 1; $index -lt $samples.Count; $index++) {
        $sampleGaps += [Math]::Round(($samples[$index].elapsedSeconds - $samples[$index - 1].elapsedSeconds), 1)
    }
    $maxSampleGap = if ($sampleGaps.Count -gt 0) { ($sampleGaps | Measure-Object -Maximum).Maximum } else { 0 }
    $frameTolerance = [Math]::Max(200, [Math]::Round($theoreticalFrames * 0.05))
    $sampleOk = $samples.Count -ge [Math]::Floor($expectedSamples * 0.9)
    $gapOk = $maxSampleGap -le ($SampleSeconds * 3)
    $frameOk = [Math]::Abs([int]$finalHealth.audio.frames - $theoreticalFrames) -le $frameTolerance

    $report = [pscustomobject]@{
        result = if ($finalHealth.capture.state -eq "idle" -and $elapsedMinutes -ge $Minutes -and $sampleOk -and $gapOk -and $frameOk) { "PASS" } else { "FAIL" }
        expectedSamples = $expectedSamples
        maxSampleGapSeconds = $maxSampleGap
        sampleOk = $sampleOk
        gapOk = $gapOk
        frameOk = $frameOk
        frameTolerance = $frameTolerance
        gitSha = $gitSha
        sidecarVersion = $sidecarVersion
        mode = $mode
        simulated = [bool]$Simulated
        startedAt = $captureStartedAtText
        endedAt = (Get-Date).ToString("o")
        theoreticalFrames = $theoreticalFrames
        actualFrames = [int]$finalHealth.audio.frames
        frameDelta = [int]$finalHealth.audio.frames - $theoreticalFrames
        windows = [ordered]@{ caption = $windows.Caption; version = $windows.Version; build = $windows.BuildNumber }
        stateTransitions = $transitions
        sidecarExitCode = $finalHealth.capture.sidecarExitCode
        elapsedMinutes = [Math]::Round($elapsedMinutes, 2)
        generatedAt = (Get-Date).ToString("o")
        minutes = $Minutes
        sampleSeconds = $SampleSeconds
        samples = $samples.Count
        captureStateFinal = $finalHealth.capture.state
        frames = [int]$finalHealth.audio.frames
        gaps = [int]$finalHealth.audio.gaps
        invalid = [int]$finalHealth.audio.invalid
        duplicates = [int]$finalHealth.audio.duplicates
        rateLimited = [int]$finalHealth.audio.rateLimited
        peakDbfs = $finalHealth.audio.peakDbfs
        averageFps = if ($last.elapsedSeconds -gt 0) { [Math]::Round($last.frames / $last.elapsedSeconds, 2) } else { $null }
        cpuSecondsGrowth = if ($first.cpuSeconds -ne $null -and $last.cpuSeconds -ne $null) { [Math]::Round($last.cpuSeconds - $first.cpuSeconds, 2) } else { $null }
        workingSetGrowthBytes = if ($first.workingSetBytes -and $last.workingSetBytes) { $last.workingSetBytes - $first.workingSetBytes } else { $null }
        privateBytesGrowthBytes = if ($first.privateBytes -and $last.privateBytes) { $last.privateBytes - $first.privateBytes } else { $null }
        workingSetMaxBytes = ($samples | Measure-Object -Property workingSetBytes -Maximum).Maximum
        privateBytesMaxBytes = ($samples | Measure-Object -Property privateBytes -Maximum).Maximum
        stopOutput = $stopOut.Trim()
        stopOk = ($finalHealth.capture.state -eq "idle")
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $jsonPath = Join-Path $OutDir "stability-$stamp.json"
    $csvPath = Join-Path $OutDir "stability-$stamp.csv"
    $report | ConvertTo-Json -Depth 5 | Set-Content $jsonPath -Encoding utf8
    $samples | Export-Csv -Path $csvPath -NoTypeInformation -Encoding utf8
    Write-Host "[stability] report: $jsonPath"
    Write-Host "[stability] samples: $csvPath"
    $report | ConvertTo-Json -Depth 3 -Compress
}
finally {
    if ($toneLoop -and -not $toneLoop.HasExited) { Stop-Process -Id $toneLoop.Id -Force -ErrorAction SilentlyContinue }
    if ($session -and -not $session.HasExited) { Stop-Process -Id $session.Id -Force -ErrorAction SilentlyContinue }
    if ($core -and -not $core.HasExited) { Stop-Process -Id $core.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
}
