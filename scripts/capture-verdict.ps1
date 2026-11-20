#Requires -Version 7.0
<#
.SYNOPSIS
  采集冒烟的三态判定逻辑（PASS / SKIP / FAIL）与自测。

.DESCRIPTION
  把判定从 capture-smoke.ps1 中抽出，便于无音频设备的环境也能验证判定表。
  使用方式：dot-source 后调用 Get-CaptureVerdict，或 -SelfTest 运行内置用例。

.EXAMPLE
  pwsh -File scripts/capture-verdict.ps1 -SelfTest
#>
param(
    [switch]$SelfTest
)

function Get-CaptureVerdict {
    param(
        [bool]$DevicePresent,
        [bool]$PlaybackFailed,
        [bool]$CaptureExited,
        [int]$Frames,
        [int]$Invalid,
        [Nullable[double]]$PeakDbfs = $null,
        [double]$SilenceThreshold = -45,
        [int]$MinimumFrames = 100
    )

    if (-not $DevicePresent) {
        return [pscustomobject]@{ Result = "SKIP"; Code = "no_audio_device"; ExitCode = 0 }
    }
    if ($PlaybackFailed) {
        return [pscustomobject]@{ Result = "FAIL"; Code = "playback_failed"; ExitCode = 1 }
    }
    if ($CaptureExited) {
        return [pscustomobject]@{ Result = "FAIL"; Code = "capture_failed"; ExitCode = 1 }
    }
    if ($Frames -le $MinimumFrames) {
        return [pscustomobject]@{ Result = "FAIL"; Code = "no_frames"; ExitCode = 1 }
    }
    if ($Invalid -ne 0) {
        return [pscustomobject]@{ Result = "FAIL"; Code = "invalid_frames"; ExitCode = 1 }
    }
    if ($null -eq $PeakDbfs -or $PeakDbfs -le $SilenceThreshold) {
        return [pscustomobject]@{ Result = "FAIL"; Code = "silent_capture"; ExitCode = 1 }
    }

    return [pscustomobject]@{ Result = "PASS"; Code = "ok"; ExitCode = 0 }
}

function Test-CaptureVerdict {
    $cases = @(
        @{ Name = "no device skips"; Args = @{ DevicePresent = $false }; Expect = "SKIP"; Code = "no_audio_device"; Exit = 0 },
        @{ Name = "playback failure fails"; Args = @{ DevicePresent = $true; PlaybackFailed = $true; Frames = 500; PeakDbfs = -10 }; Expect = "FAIL"; Code = "playback_failed"; Exit = 1 },
        @{ Name = "capture crash fails"; Args = @{ DevicePresent = $true; CaptureExited = $true }; Expect = "FAIL"; Code = "capture_failed"; Exit = 1 },
        @{ Name = "no frames fails"; Args = @{ DevicePresent = $true; Frames = 0; PeakDbfs = -10 }; Expect = "FAIL"; Code = "no_frames"; Exit = 1 },
        @{ Name = "invalid frames fail"; Args = @{ DevicePresent = $true; Frames = 500; Invalid = 2; PeakDbfs = -10 }; Expect = "FAIL"; Code = "invalid_frames"; Exit = 1 },
        @{ Name = "silent capture fails"; Args = @{ DevicePresent = $true; Frames = 500; PeakDbfs = -120 }; Expect = "FAIL"; Code = "silent_capture"; Exit = 1 },
        @{ Name = "null peak fails"; Args = @{ DevicePresent = $true; Frames = 500 }; Expect = "FAIL"; Code = "silent_capture"; Exit = 1 },
        @{ Name = "healthy capture passes"; Args = @{ DevicePresent = $true; Frames = 500; PeakDbfs = -12 }; Expect = "PASS"; Code = "ok"; Exit = 0 },
        @{ Name = "threshold boundary fails"; Args = @{ DevicePresent = $true; Frames = 500; PeakDbfs = -45 }; Expect = "FAIL"; Code = "silent_capture"; Exit = 1 }
    )

    $failures = 0
    foreach ($case in $cases) {
        $params = $case.Args
        $actual = Get-CaptureVerdict @params
        $ok = ($actual.Result -eq $case.Expect) -and ($actual.Code -eq $case.Code) -and ($actual.ExitCode -eq $case.Exit)
        if (-not $ok) {
            Write-Host "[verdict] FAIL $($case.Name): got $($actual.Result)/$($actual.Code)/$($actual.ExitCode)"
            $failures++
        }
        else {
            Write-Host "[verdict] ok $($case.Name)"
        }
    }

    if ($failures -gt 0) {
        Write-Host "[verdict] $failures case(s) failed"
        return 1
    }

    Write-Host "[verdict] PASS all $($cases.Count) cases"
    return 0
}

if ($SelfTest) {
    exit (Test-CaptureVerdict)
}
