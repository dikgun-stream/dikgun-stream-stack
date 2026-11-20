# Back up the machine-local state that git does not hold.
#
#   powershell -File backup.ps1
#   powershell -File backup.ps1 -Dest "E:\dikgun-backup"
#
# Git holds the code. It deliberately does not hold:
#   * overlay-server/config.json      channel settings, titles, checklists
#   * the three scanner indexes       hours of CPU to rebuild
#   * the OBS scene collection        every manual crop and position
#   * discord-bot/.env                the bot token
#
# Of those, only the scene collection and the indexes are painful to lose, and
# the scene collection is unrecoverable - build_scenes.py produces a scaffold,
# not the same file.
#
# The Discord token is deliberately NOT copied. .env.template shows the keys.

[CmdletBinding()]
param(
    [string]$Dest
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $Dest) { $Dest = Join-Path (Split-Path -Parent $here) "dikgun-backup-$stamp" }

$APPDATA_DIR = $env:APPDATA

# The repo copy of overlay-server is not necessarily the one the server runs
# from - on this machine the live install is a sibling of the repo. Picking the
# wrong one silently backs up a stale config, so choose by newest config.json.
$serverCandidates = @(
    (Join-Path $here 'overlay-server'),
    (Join-Path (Split-Path -Parent $here) 'overlay-server')
)
$serverDir = $null
$newest = [datetime]::MinValue
foreach ($c in $serverCandidates) {
    $cfg = Join-Path $c 'config.json'
    if (Test-Path $cfg) {
        $t = (Get-Item $cfg).LastWriteTime
        if ($t -gt $newest) { $newest = $t; $serverDir = $c }
    }
}
if (-not $serverDir) { $serverDir = Join-Path $here 'overlay-server' }
Write-Host "  server dir : $serverDir" -ForegroundColor DarkGray

$items = @(
    @{ Src = (Join-Path $serverDir 'config.json');          Dst = 'config.json' }
    @{ Src = (Join-Path $serverDir 'laughter_index.json');  Dst = 'laughter_index.json' }
    @{ Src = (Join-Path $serverDir 'kill_index.json');      Dst = 'kill_index.json' }
    @{ Src = (Join-Path $serverDir 'marker_index.json');    Dst = 'marker_index.json' }
    @{ Src = (Join-Path $APPDATA_DIR 'obs-studio\basic\scenes\DikGun.json'); Dst = 'obs\DikGun.json' }
    @{ Src = (Join-Path $APPDATA_DIR 'obs-studio\plugin_config\vertical-canvas\config.json'); Dst = 'obs\vertical-canvas.json' }
    @{ Src = (Join-Path $APPDATA_DIR 'obs-studio\plugin_config\aitum-multistream\config.json'); Dst = 'obs\aitum-multistream.json' }
    @{ Src = (Join-Path $APPDATA_DIR 'obs-studio\global.ini');           Dst = 'obs\global.ini' }
)

Write-Host ''
Write-Host '  DikGun machine-state backup' -ForegroundColor Cyan
Write-Host '  ==========================='
Write-Host "  to: $Dest"
Write-Host ''

New-Item -ItemType Directory -Path $Dest -Force | Out-Null
$copied = 0
$missing = @()

foreach ($i in $items) {
    if (-not (Test-Path $i.Src)) { $missing += $i.Dst; continue }
    $target = Join-Path $Dest $i.Dst
    $dir = Split-Path -Parent $target
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item $i.Src $target -Force
    $kb = [math]::Round((Get-Item $target).Length / 1KB, 1)
    Write-Host ("  ok      {0,-34} {1,8} KB" -f $i.Dst, $kb) -ForegroundColor Green
    $copied++
}

# the Stream Dock profile is in git, but the INSTALLED copy may have been edited
$sd = Join-Path $APPDATA_DIR 'HotSpot\StreamDock\profiles'
if (Test-Path $sd) {
    $installed = $null
    foreach ($d in (Get-ChildItem $sd -Directory | Where-Object { $_.Name -notmatch 'backup' })) {
        $mf = Join-Path $d.FullName 'manifest.json'
        if (-not (Test-Path $mf)) { continue }
        try {
            $j = Get-Content $mf -Raw | ConvertFrom-Json
            if ($j.Name -eq 'DikGun Stream') { $installed = $d; break }
        } catch { }
    }
    if (-not $installed) {
        Write-Host '  !! could not find a Stream Dock profile named "DikGun Stream"' -ForegroundColor Yellow
    }
    if ($installed) {
        $target = Join-Path $Dest 'streamdock'
        Copy-Item $installed.FullName $target -Recurse -Force
        Write-Host ("  ok      {0,-34} {1}" -f 'streamdock/', $installed.Name) -ForegroundColor Green
        $copied++
    }
}

# a token-free template so the keys are remembered
$envSrc = Join-Path $here 'discord-bot\.env'
if (Test-Path $envSrc) {
    $lines = Get-Content $envSrc | ForEach-Object {
        if ($_ -match '^\s*(DISCORD_TOKEN|DISCORD_GUILD_ID)\s*=\s*(.+)$') {
            "$($Matches[1])=(redacted)"
        } else { $_ }
    }
    Set-Content -Path (Join-Path $Dest 'env.template') -Value $lines -Encoding UTF8
    Write-Host ("  ok      {0,-34} {1}" -f 'env.template', '(token removed)') -ForegroundColor Green
    $copied++
}

Write-Host ''
if ($missing.Count) {
    Write-Host "  not found (not an error if this is a fresh machine):" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "     $_" -ForegroundColor DarkGray }
    Write-Host ''
}
Write-Host "  copied $copied item(s)" -ForegroundColor Green
Write-Host ''
Write-Host '  Keep this folder off this machine - a private repo, a USB stick, cloud' -ForegroundColor Cyan
Write-Host '  storage. A dead boot drive otherwise costs you the scene collection,' -ForegroundColor Cyan
Write-Host '  which cannot be rebuilt identically.' -ForegroundColor Cyan
Write-Host ''
