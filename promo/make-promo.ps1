# Build a highlight video in one command.
#
#   powershell -File make-promo.ps1 -Music "black-sky.m4a" -Game "Apex Legends" -Duration 60
#   powershell -File make-promo.ps1 -Music song.mp3 -Game all -Duration 90 -Out promo.mp4
#   powershell -File make-promo.ps1 -Music song.mp3 -Game "Deadlock" -Start 42 -DryRun
#   powershell -File make-promo.ps1 -Music song.mp3 -Game all -Mode edit -Duration 180
#
# -Mode promo (default) cuts the DIKGUN letter cards in on the accents.
# -Mode edit is the same cut, same kill/music interplay, just no letter cards -
# for regular music edits rather than a branded promo.
#
# Wrap of promo_build.py that fills in the paths and checks the things that
# actually fail: the music file, and the overlay server that supplies kill
# windows. Everything else passes straight through.
#
# Song windows worth knowing for a 4-5 minute track: the build and the drop are in
# different places. --Start picks where in the track the 60/90 seconds begin.
# For Black Sky: 42 = build then drop, 78 = straight into the drop.

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][string]$Music,
    [string[]]$Game = @("all"),
    [double]$Duration = 60,
    [double]$Start = 0,
    [string]$Out = "",
    [string]$Outro = "",
    [string]$Prefer = "",
    [string]$Pin = "",
    [ValidateSet('promo', 'edit')][string]$Mode = 'promo',
    [string]$SaveEdl = "",
    [string]$FromEdl = "",
    [switch]$NoCache,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "python not found" }

# resolve relative paths against the script directory too, so the command works
# from any working directory
function Resolve-Arg([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return $p }
    if (Test-Path $p) { return $p }
    $alt = Join-Path $here $p
    if (Test-Path $alt) { return $alt }
    return $p
}
$Music  = Resolve-Arg $Music
$Outro  = Resolve-Arg $Outro
$FromEdl = Resolve-Arg $FromEdl

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

# -Game accepts either one comma-separated string or several tokens. With
# `powershell -File`, "A","B" arrives as literal "A," and "B", so normalise:
# split on commas, trim, drop blanks, rejoin.
$gameParts = @()
foreach ($g in $Game) { $gameParts += ($g -split ',') }
$GameCsv = (($gameParts | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ',')
if (-not $GameCsv) { $GameCsv = 'all' }

# A stray game name binding into -Out is what happens when several arguments are
# passed to -Game; ffmpeg then fails with "Unable to find a suitable output
# format" for the game name. Catch it here with something actionable instead.
if ($Out -and $Out -notmatch '\.(mp4|mkv|mov|webm)$') {
    throw ("-Out '$Out' does not look like a video filename. " +
           "If you meant several games, pass them as ONE comma-separated string: " +
           "-Game 'Apex Legends,Rainbow Six Siege,Battlefield 6'")
}

if (-not $Out) {
    $slug = ($GameCsv -replace '[^A-Za-z0-9]+', '-').Trim('-').ToLower()
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

if ($GameCsv -and $GameCsv -ne 'all') {
    $args += @('--games', $GameCsv)
}
$args += @('--mode', $Mode)
if ($Prefer) { $args += @('--prefer', $Prefer) }
if ($Pin)    { $args += @('--pin', $Pin) }
if ($Outro) {
    if (-not (Test-Path $Outro)) { throw "outro not found: $Outro" }
    $args += @('--outro', $Outro)
}
if ($FromEdl -and -not (Test-Path $FromEdl)) { throw "EDL not found: $FromEdl" }
if ($SaveEdl) { $args += @('--save-edl', $SaveEdl) }
if ($FromEdl) { $args += @('--from-edl', $FromEdl) }
if ($NoCache) { $args += '--no-cache' }
if ($DryRun) { $args += '--dry-run' }

Write-Host ""
Write-Host "  game     : $GameCsv"
Write-Host "  music    : $Music"
Write-Host "  window   : ${Start}s + ${Duration}s"
Write-Host "  mode     : $Mode"
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
