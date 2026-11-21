# Build a highlight video in one command.
#
#   powershell -File make-promo.ps1 -Music "black-sky.m4a" -Game "Apex Legends" -Duration 60
#   powershell -File make-promo.ps1 -Music song.mp3 -Game all -Duration 90 -Out promo.mp4
#   powershell -File make-promo.ps1 -Music song.mp3 -Game "Deadlock" -Start 42 -DryRun
#
# Wrap of promo_build.py that fills in the paths and checks the things that
# actually fail: the music file, and the overlay server that supplies kill
# windows. Everything else passes straight through.
#
# Song windows worth knowing for a 4-5 minute track: the build and the drop are in
# different places. --Start picks where in the track the 60/90 seconds begin.
# For Black Sky: 42 = build then drop, 78 = straight into the drop.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Music,
    [string]$Game = "all",
    [double]$Duration = 60,
    [double]$Start = 0,
    [string]$Out = "",
    [string]$Outro = "",
    [string]$Prefer = "",
    [string]$Pin = "",
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "python not found" }

if (-not (Test-Path $Music)) { throw "music not found: $Music" }

# the kill windows come from the overlay server, so say so plainly rather than
# letting promo_build fail with a stack trace
try {
    $null = Invoke-RestMethod 'http://127.0.0.1:7475/api/clips/highlights?source=all&limit=5' -TimeoutSec 20
} catch {
    Write-Host "  overlay server is not answering on 127.0.0.1:7475" -ForegroundColor Yellow
    Write-Host "  start it first:" -ForegroundColor Yellow
    Write-Host "    cd overlay-server; python -m uvicorn server:app --host 127.0.0.1 --port 7475" -ForegroundColor DarkGray
    throw "overlay server required"
}

if (-not $Out) {
    $slug = ($Game -replace '[^A-Za-z0-9]+', '-').Trim('-').ToLower()
    if (-not $slug -or $slug -eq 'all') { $slug = 'mixed' }
    $Out = "promo-$slug-$([int]$Duration)s.mp4"
}

$args = @(
    (Join-Path $here 'promo_build.py'),
    '--music', $Music,
    '--duration', $Duration,
    '--start', $Start,
    '--out', $Out
)

if ($Game -and $Game -ne 'all') {
    $args += @('--games', $Game)
}
if ($Prefer) { $args += @('--prefer', $Prefer) }
if ($Pin)    { $args += @('--pin', $Pin) }
if ($Outro) {
    if (-not (Test-Path $Outro)) { throw "outro not found: $Outro" }
    $args += @('--outro', $Outro)
}
if ($DryRun) { $args += '--dry-run' }

Write-Host ""
Write-Host "  game     : $Game"
Write-Host "  music    : $Music"
Write-Host "  window   : ${Start}s + ${Duration}s"
Write-Host "  output   : $Out"
if ($Outro) { Write-Host "  outro    : $Outro" }
Write-Host ""

& $python @args
if ($LASTEXITCODE -ne 0) { throw "promo_build failed (exit $LASTEXITCODE)" }

if (-not $DryRun -and (Test-Path $Out)) {
    $mb = [math]::Round((Get-Item $Out).Length / 1MB, 1)
    Write-Host ""
    Write-Host "  wrote $Out  ($mb MB)" -ForegroundColor Green
}
