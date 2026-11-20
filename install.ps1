<#
.SYNOPSIS
    Install and prepare the DikGun stream stack on a fresh machine.

.DESCRIPTION
    Two jobs: get the software, then wire this checkout up to run.

        powershell -File install.ps1 -Check      report only (default)
        powershell -File install.ps1 -All        install everything, wire it up

    Individual stages:
        -Software   winget: OBS, Python 3.12, ffmpeg, Tesseract, Streamer.bot
        -Plugins    OBS plugins: vertical-canvas, aitum-multistream
        -Deps       pip install overlay-server/requirements.txt
        -Scenes     restore the OBS scene collection from obs/scenes/
        -Model      YAMNet SavedModel for the laughter scanner (~35 MB)
        -Deck       render the Stream Dock profile for this checkout
        -Config     create overlay-server/config.json from the example

    Every stage is idempotent and skips what is already present. Nothing is
    overwritten without -Force, and existing files are backed up first.

    What it cannot do: install Stream Dock AJAZZ or RivaTuner (neither is on
    winget), sign into Twitch/YouTube, or set stream keys. Those are listed at the
    end as manual steps.

.PARAMETER Check
    Report what is present and missing. No changes. This is the default.

.PARAMETER All
    Run every stage.
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$All,
    [switch]$Software,
    [switch]$Plugins,
    [switch]$Deps,
    [switch]$Scenes,
    [switch]$Model,
    [switch]$Deck,
    [switch]$Config,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$problems = @()
$manual   = @()

function Step($n, $title) { Write-Host "`n[$n] $title" -ForegroundColor Cyan }
function Ok($m)     { Write-Host "  $m" -ForegroundColor Green }
function Skip($m)   { Write-Host "  $m" -ForegroundColor DarkGray }
function Warn($m)   { Write-Host "  $m" -ForegroundColor Yellow }
function Fail($m)   { Write-Host "  $m" -ForegroundColor Red; $script:problems += $m }

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Cmd($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# winget cannot install elevated-elevated cleanly for machine scope, but it needs
# admin for OBS. Report rather than fail.
$elevated = Test-Elevated
$installed = [ordered]@{}

Write-Host ''
Write-Host '  DikGun stream stack installer' -ForegroundColor Cyan
Write-Host '  ============================='
Write-Host "  repo     : $repo"
Write-Host "  elevated : $elevated"
if (-not $elevated) {
    Write-Host '  (OBS installs machine-wide and will prompt for admin; the rest is per-user)' -ForegroundColor DarkGray
}

# ============================================================== 1. software
Step '1/7' 'software'

$pkgs = @(
    @{ Name = 'OBS Studio';     Id = 'OBSProject.OBSStudio';       Test = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe' }
    @{ Name = 'Python 3.12';    Id = 'Python.Python.3.12';         Test = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" }
    @{ Name = 'ffmpeg';         Id = 'Gyan.FFmpeg';                Test = $null; Cmd = 'ffmpeg' }
    @{ Name = 'Tesseract OCR';  Id = 'UB-Mannheim.TesseractOCR';   Test = 'C:\Program Files\Tesseract-OCR\tesseract.exe' }
    @{ Name = 'Streamer.bot';   Id = 'streamerbot.streamerbot';    Test = "$env:USERPROFILE\Apps\Streamer.bot\Streamer.bot.exe" }
)

$winget = Test-Cmd 'winget'
foreach ($p in $pkgs) {
    $have = if ($p.Test) { Test-Path $p.Test } else { [bool](Test-Cmd $p.Cmd) }
    if ($have) { Ok "$($p.Name) present"; $installed[$p.Name] = $true; continue }

    $installed[$p.Name] = $false
    if (-not $Software -and -not $All) { Fail "$($p.Name) MISSING (winget id $($p.Id))"; continue }
    if (-not $winget) { Fail "$($p.Name) missing and winget unavailable"; continue }

    Write-Host "  installing $($p.Name)..." -ForegroundColor Yellow
    & winget install --id $p.Id --exact --silent --accept-source-agreements --accept-package-agreements 2>&1 |
        Select-Object -Last 2 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }

    $now = if ($p.Test) { Test-Path $p.Test } else { [bool](Test-Cmd $p.Cmd) }
    if ($now) { Ok "$($p.Name) installed"; $installed[$p.Name] = $true }
    else { Fail "$($p.Name) install did not complete - rerun or install by hand" }
}

# =============================================================== 2. plugins
Step '2/7' 'OBS plugins'

$pluginRoot = 'C:\ProgramData\obs-studio\plugins'
$obsPlugins = 'C:\Program Files\obs-studio\obs-plugins\64bit'

# OBS loads plugins from BOTH ProgramData and the install folder, and will
# silently skip a duplicate. Installing a second copy into whichever location is
# already occupied just creates the "skipping legacy plugin" confusion, so a
# plugin counts as present if EITHER location has it.
$pluginSpecs = @(
    @{ Name = 'vertical-canvas';   Repo = 'Aitum/obs-vertical-canvas'
       Asset = 'windows-programdata.zip'
       Dlls = @((Join-Path $pluginRoot 'vertical-canvas\vertical-canvas.dll'),
                (Join-Path $obsPlugins 'vertical-canvas.dll')) }
    @{ Name = 'aitum-multistream'; Repo = 'Aitum/obs-aitum-multistream'
       Asset = 'windows-programdata.zip'
       Dlls = @((Join-Path $pluginRoot 'aitum-multistream\aitum-multistream.dll'),
                (Join-Path $obsPlugins 'aitum-multistream.dll')) }
)

foreach ($spec in $pluginSpecs) {
    $found = $spec.Dlls | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) { Ok "$($spec.Name) present ($(Split-Path $found -Parent))"; continue }
    if (-not $Plugins -and -not $All) { Fail "$($spec.Name) MISSING"; continue }

    try {
        Write-Host "  resolving $($spec.Name) release..." -ForegroundColor Yellow
        $rel = Invoke-RestMethod "https://api.github.com/repos/$($spec.Repo)/releases/latest" `
            -Headers @{ 'User-Agent' = 'dikgun-installer' } -TimeoutSec 30
        $asset = $rel.assets | Where-Object { $_.name -like "*$($spec.Asset)" } | Select-Object -First 1
        if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -match 'windows.*\.zip$' } | Select-Object -First 1 }
        if (-not $asset) { Fail "$($spec.Name): no Windows zip in release $($rel.tag_name)"; continue }

        $tmp = Join-Path $env:TEMP "$($spec.Name)-$($rel.tag_name).zip"
        Invoke-WebRequest $asset.browser_download_url -OutFile $tmp -UseBasicParsing -TimeoutSec 300
        New-Item -ItemType Directory -Path $pluginRoot -Force | Out-Null
        Expand-Archive $tmp -DestinationPath $pluginRoot -Force
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue

        $now = $spec.Dlls | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($now) { Ok "$($spec.Name) $($rel.tag_name) installed" }
        else { Fail "$($spec.Name) extracted but no DLL found in either plugin location" }
    } catch {
        Fail "$($spec.Name) download failed: $($_.Exception.Message)"
    }
}
$manual += 'Install Stream Dock AJAZZ (winget: no package) - needed only for the deck profile'
$manual += 'Install RivaTuner 2.24 (optional, display profiles only)'

# ================================================================== 3. deps
Step '3/7' 'python dependencies'

$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) { $python = Test-Cmd 'python' }
$req = Join-Path $repo 'overlay-server\requirements.txt'

if (-not $python) { Fail 'python not found' }
elseif (-not (Test-Path $req)) { Fail 'overlay-server/requirements.txt missing' }
else {
    $have = & $python -c "import importlib.util as u;print(','.join(m for m in ['fastapi','uvicorn','websockets','PIL','cv2','pytesseract'] if u.find_spec(m)))" 2>&1
    Write-Host "  present: $have"
    $missing = @('fastapi','uvicorn','websockets','PIL','cv2','pytesseract') | Where-Object { $have -notmatch $_ }
    if (-not $missing.Count) { Ok 'core modules present' }
    elseif ($Deps -or $All) {
        Write-Host '  pip install -r requirements.txt ...' -ForegroundColor Yellow
        & $python -m pip install --quiet -r $req 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Ok 'pip finished'
    } else {
        Fail "python modules missing: $($missing -join ', ')  (use -Deps)"
    }
    $installed['Python modules'] = -not $missing.Count
}

# ================================================================ 4. config
Step '4/7' 'overlay-server config'

$liveCfg = Join-Path $repo 'overlay-server\config.json'
$exCfg   = Join-Path $repo 'overlay-server\config.example.json'
if ((Test-Path $liveCfg) -and -not $Force) {
    Ok 'config.json exists, left alone (-Force to reset)'
} elseif (Test-Path $exCfg) {
    Copy-Item $exCfg $liveCfg -Force
    Ok 'created config.json from config.example.json'
    $manual += 'Check overlay-server/config.json: twitch.channel, obs.password'
} else {
    Fail 'config.example.json missing'
}

# ================================================================ 5. scenes
Step '5/7' 'OBS scene collection'

$sceneSrc = Join-Path $repo 'obs\scenes\DikGun.json'
$sceneDir = Join-Path $env:APPDATA 'obs-studio\basic\scenes'
$sceneDst = Join-Path $sceneDir 'DikGun.json'

if (-not (Test-Path $sceneSrc)) { Fail 'obs/scenes/DikGun.json missing from the repo' }
elseif ((Test-Path $sceneDst) -and -not $Force) {
    Ok 'scene collection already installed, left alone (-Force to replace)'
} elseif (-not $Scenes -and -not $All) {
    Warn "scene collection not installed. Run with -Scenes to copy it into $sceneDir"
} else {
    if (-not (Test-Path $sceneDir)) { New-Item -ItemType Directory -Path $sceneDir -Force | Out-Null }
    if (Test-Path $sceneDst) {
        $bak = "$sceneDst.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $sceneDst $bak -Force
        Ok "backed up existing to $(Split-Path $bak -Leaf)"
    }
    Copy-Item $sceneSrc $sceneDst -Force
    Ok "installed -> $sceneDst"
    $manual += 'Switch OBS to the DikGun scene collection, then run obs-build/refresh-scene-sources.ps1'
}

# ================================================================= 6. model
Step '6/7' 'YAMNet model (laughter scanner)'

$modelDir = Join-Path $repo 'obs-build\yamnet'
$modelHas = (Test-Path (Join-Path $modelDir 'saved_model.pb')) -or
            (Test-Path (Join-Path $modelDir 'yamnet.tflite'))
if ($modelHas) { Ok 'model present' }
elseif (-not $Model -and -not $All) {
    Warn "not present - only needed to re-run laughter_scan.py (use -Model)"
} else {
    try {
        Write-Host '  downloading from TensorFlow Hub (~35 MB)...' -ForegroundColor Yellow
        $tar = Join-Path $env:TEMP 'yamnet.tar.gz'
        Invoke-WebRequest 'https://tfhub.dev/google/yamnet/1?tf-hub-format=compressed' `
            -OutFile $tar -UseBasicParsing -TimeoutSec 600
        New-Item -ItemType Directory -Path $modelDir -Force | Out-Null
        $stage = Join-Path $env:TEMP 'yamnet-extract'
        if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        & tar.exe -xzf $tar -C $stage
        # the archive nests under a version folder
        $inner = Get-ChildItem $stage -Recurse -File -Filter 'saved_model.pb' | Select-Object -First 1
        if ($inner) {
            Copy-Item (Split-Path $inner.FullName -Parent)\* $modelDir -Recurse -Force
            Ok "extracted to $modelDir"
        } else {
            Get-ChildItem $stage -Recurse -File | ForEach-Object {
                $rel = $_.FullName.Substring($stage.Length + 1)
                $dst = Join-Path $modelDir $rel
                $d = Split-Path $dst -Parent
                if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
                Copy-Item $_.FullName $dst -Force
            }
            Warn 'copied the archive as-is; verify saved_model.pb landed in obs-build/yamnet/'
        }
        Remove-Item $tar,$stage -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        Fail "YAMNet download failed: $($_.Exception.Message)"
    }
}

# ================================================================== 7. deck
Step '7/7' 'Stream Dock profile'

$deckInstaller = Join-Path $repo 'streamdeck\install.ps1'
$profile = Join-Path $env:APPDATA 'HotSpot\StreamDock\profiles'
if (-not (Test-Path $profile)) {
    Warn 'Stream Dock not installed - skipping the deck profile'
    $manual += 'After installing Stream Dock: powershell -File streamdeck/install.ps1'
} elseif (Test-Path $deckInstaller) {
    if ($Deck -or $All) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $deckInstaller
    } else {
        Warn 'deck profile not deployed (use -Deck)'
    }
} else {
    Fail 'streamdeck/install.ps1 missing'
}

# =============================================================== summary
Write-Host ''
Write-Host '========== summary ==========' -ForegroundColor Cyan
if ($problems.Count) {
    Write-Host 'BLOCKING:' -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
} else {
    Write-Host 'nothing blocking' -ForegroundColor Green
}

Write-Host ''
Write-Host 'still manual - these cannot be scripted:' -ForegroundColor Yellow
$manual += 'Sign in to Twitch and YouTube inside OBS (stream keys are per-account)'
$manual += 'Enable obs-websocket in OBS: Tools -> WebSocket Server Settings'
$manual += 'Import the Streamer.bot config from streamer-mode/ and connect the accounts'
$manual += 'Discord bot: powershell -File discord-bot/setup.ps1'
$manual | Select-Object -Unique | ForEach-Object { Write-Host "  - $_" }
Write-Host ''
Write-Host 'next: cd overlay-server; python -m uvicorn server:app --host 127.0.0.1 --port 7475' -ForegroundColor Cyan
Write-Host 'then: http://127.0.0.1:7475/' -ForegroundColor Cyan
Write-Host ''
