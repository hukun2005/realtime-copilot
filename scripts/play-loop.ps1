param(
    [Parameter(Mandatory = $true)][string]$Wav,
    [int]$Seconds = 3600
)
$player = New-Object System.Media.SoundPlayer $Wav
$player.PlayLooping()
Start-Sleep -Seconds $Seconds
$player.Stop()
