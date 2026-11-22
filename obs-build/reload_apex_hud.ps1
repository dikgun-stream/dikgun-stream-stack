# Apply the Apex HUD insets to the scene collection and reload OBS.
#
#   powershell -File reload_apex_hud.ps1              # apply, then relaunch OBS
#   powershell -File reload_apex_hud.ps1 -KeepClosed  # apply, leave OBS closed
#   powershell -File reload_apex_hud.ps1 -DryRun      # show the plan, touch nothing
#
# WHY THIS EXISTS
# OBS reads the scene collection once at startup and keeps it in memory. Writing
# DikGun.json while OBS is open therefore does two bad things:
#
#   1. the running OBS never sees the change, so it looks like nothing happened
#   2. when OBS exits it writes ITS copy back over the file, destroying the edit
#
# That is exactly what happened the first time: the script wrote the insets, OBS
# was still open, and its shutdown save wiped them minutes later.
#
# So the order must be: stop OBS first, write the file, then start OBS.
#
# OBS is stopped with Stop-Process rather than a normal close, deliberately: a
# normal close saves the collection first, which is the thing we are trying to
# avoid. The trade-off is that any UNRELATED unsaved OBS changes from this session
# are discarded. The scene collection itself is backed up by the Python script
# before it writes.

[CmdletBinding()]
param(
    [switch]$KeepClosed,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) { throw 'python not found' }

$obs = Get-Process obs64 -ErrorAction SilentlyContinue
$streaming = $false
if ($obs) {
    Write-Host "  OBS running (pid $($obs.Id))" -ForegroundColor Yellow
    if ($DryRun) {
        Write-Host '  dry run - leaving OBS alone, showing only the plan' -ForegroundColor DarkGray
    }
}

if ($DryRun) {
    & $python (Join-Path $here 'add_apex_hud_insets.py')
    exit $LASTEXITCODE
}

# Refuse to pull OBS out from under a live stream.
if ($obs) {
    try {
        $r = Invoke-WebRequest 'http://127.0.0.1:4455' -TimeoutSec 2 -ErrorAction Stop
    } catch { }
    $ws = Get-NetTCPConnection -LocalPort 4455 -State Listen -ErrorAction SilentlyContinue
    if ($ws) {
        Write-Host ''
        Write-Host '  obs-websocket is listening. If you are LIVE or RECORDING, stop first.' -ForegroundColor Yellow
        $answer = Read-Host '  Type LIVE to continue anyway, or anything else to abort'
        if ($answer -ne 'LIVE') { Write-Host '  aborted, nothing changed'; exit 1 }
    }
    Write-Host '  stopping OBS without saving (so it cannot overwrite the collection)'
    Stop-Process -Id $obs.Id -Force
    Start-Sleep -Seconds 4
    if (Get-Process obs64 -ErrorAction SilentlyContinue) {
        throw 'OBS did not exit - close it manually and re-run'
    }
}

Write-Host '  writing the collection'
& $python (Join-Path $here 'add_apex_hud_insets.py') --apply
if ($LASTEXITCODE -ne 0) { throw "inset script failed (exit $LASTEXITCODE)" }

if ($KeepClosed) {
    Write-Host ''
    Write-Host '  applied. Start OBS when ready.' -ForegroundColor Green
    exit 0
}

$exe = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe'
if (Test-Path $exe) {
    Write-Host '  starting OBS'
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent)
    Write-Host ''
    Write-Host '  done - open V_GAME_Apex and the insets should be there.' -ForegroundColor Green
} else {
    Write-Host "  OBS exe not found at $exe - start it yourself" -ForegroundColor Yellow
}
