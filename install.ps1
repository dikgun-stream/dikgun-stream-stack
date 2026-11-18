<#
.SYNOPSIS
    Prepare this checkout to run on this machine.

.DESCRIPTION
    Idempotent. Safe to re-run: it never overwrites an existing config.json, and
    it backs up the Stream Dock profile before rendering the template.

    It does NOT touch stream keys, OBS profiles, or the scene collection -- those
    live in %APPDATA%\obs-studio and must be set up through OBS itself.
#>
[CmdletBinding()]
param(
    [string]$ProfileId = 'MRX4M90L-GH25-1C1J-QU4E-HBQZCSII3GQA',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Write-Host "repo: $repo" -ForegroundColor Cyan

$problems = @()
$notes    = @()

# ------------------------------------------------------------------ 1. tools
Write-Host "`n[1/4] tools" -ForegroundColor Cyan
$pwshVer = $PSVersionTable.PSVersion
if ($pwshVer.Major -lt 7) { $problems += "PowerShell $pwshVer - the deck wrappers call 'pwsh' (7+)" }
else { Write-Host "  pwsh $pwshVer ok" }

$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) {
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { $python = $c.Source } else { $python = $null }
}
if ($python) {
    Write-Host "  python: $python"
    $mods = & $python -c "import importlib.util as u;print(','.join(m for m in ['fastapi','uvicorn','websockets','PIL'] if u.find_spec(m)))" 2>&1
    $have = "$mods".Trim()
    foreach ($m in @('fastapi','uvicorn','websockets','PIL')) {
        if ($have -notmatch [regex]::Escape($m)) { $problems += "python module missing: $m  (pip install fastapi uvicorn websockets pillow)" }
    }
    if ($have -match 'fastapi' -and $have -match 'uvicorn' -and $have -match 'websockets') {
        Write-Host "  python modules ok: $have"
    }
} else {
    $problems += "python 3.12 not found"
}

# --------------------------------------------------------------- 2. config
Write-Host "`n[2/4] overlay-server/config.json" -ForegroundColor Cyan
$live = Join-Path $repo 'overlay-server\config.json'
$example = Join-Path $repo 'overlay-server\config.example.json'
if ((Test-Path $live) -and (-not $Force)) {
    Write-Host "  exists, left alone (use -Force to reset)" -ForegroundColor Yellow
} elseif (Test-Path $example) {
    Copy-Item $example $live -Force
    Write-Host "  created from config.example.json" -ForegroundColor Green
    $notes += "edit overlay-server/config.json: twitch.channel, obs.password, panic.scene"
} else {
    $problems += "config.example.json missing from the repo"
}

# ----------------------------------------------------------- 3. deck profile
Write-Host "`n[3/4] Stream Dock profile" -ForegroundColor Cyan
$template = Join-Path $repo 'streamer-mode\deck\manifest.template.json'
$profilesRoot = Join-Path $env:APPDATA 'HotSpot\StreamDock\profiles'
$target = Join-Path $profilesRoot "$ProfileId.sdProfile\manifest.json"

if (-not (Test-Path $template)) {
    Write-Host "  no template, skipping" -ForegroundColor Yellow
} elseif (-not (Test-Path $profilesRoot)) {
    Write-Host "  Stream Dock not installed here - skipping" -ForegroundColor Yellow
} else {
    $json = Get-Content $template -Raw | ConvertFrom-Json
    $json.Actions.PSObject.Properties | ForEach-Object {
        $p = $_.Value.Settings.path
        if ($p -and $p -like '*{{REPO}}*') {
            $_.Value.Settings.path = $p.Replace('{{REPO}}', $repo)
        }
    }
    New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
    if (Test-Path $target) {
        $bak = "$target.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $target $bak -Force
        Write-Host "  backed up existing: $(Split-Path $bak -Leaf)"
    }
    $json | ConvertTo-Json -Depth 12 | Set-Content $target -Encoding utf8
    Write-Host "  rendered -> $target" -ForegroundColor Green
    $notes += "restart Stream Dock AJAZZ so it reloads the profile"
}

# ---------------------------------------------------------------- 4. OBS link
Write-Host "`n[4/4] OBS websocket" -ForegroundColor Cyan
$port = 4455
if (Test-Path $live) {
    try { $cfg = Get-Content $live -Raw | ConvertFrom-Json; if ($cfg.obs.port) { $port = [int]$cfg.obs.port } } catch { }
}
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $ok = $tcp.ConnectAsync('127.0.0.1', $port).Wait(300)
    if ($ok) { Write-Host "  127.0.0.1:$port is listening" -ForegroundColor Green }
    else { Write-Host "  127.0.0.1:$port not listening" -ForegroundColor Yellow; $notes += "enable obs-websocket in OBS (Tools -> WebSocket Server Settings)" }
} catch {
    Write-Host "  probe failed: $($_.Exception.Message)" -ForegroundColor Yellow
} finally { $tcp.Dispose() }

# ------------------------------------------------------------------ summary
Write-Host "`n========== summary ==========" -ForegroundColor Cyan
if ($problems.Count) {
    Write-Host "BLOCKING:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
} else {
    Write-Host "no blocking problems" -ForegroundColor Green
}
if ($notes.Count) {
    Write-Host "`nstill to do by hand:" -ForegroundColor Yellow
    $notes | ForEach-Object { Write-Host "  - $_" }
    Write-Host "  - see SETUP.md sections 4-6 for machine paths, game launchers, OBS scenes"
}
Write-Host "`nnext: cd overlay-server; python -m uvicorn server:app --host 127.0.0.1 --port 7475"
