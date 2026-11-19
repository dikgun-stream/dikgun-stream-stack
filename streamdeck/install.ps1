# Installs the DikGun Stream profile into Stream Dock (AJAZZ / HotSpot).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -WhatIf
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Target C:\temp\test
#
# The profile is a folder tree, not a single file, and its manifests contain
# absolute paths to the deck scripts. Copying alone would install buttons that
# point at the wrong place on any other machine, so the paths are rewritten.

param(
    [switch]$WhatIf,
    [string]$Target = ''
)

$ErrorActionPreference = 'Stop'

# The installed folder name must stay the same: both manifests reference this
# GUID by name in Pages.Current / Pages.Pages, so renaming breaks the profile.
$ProfileId = 'MRX4M90L-GH25-1C1J-QU4E-HBQZCSII3GQA'

$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$source = Join-Path $here 'profile\DikGun Stream.sdProfile'
$repoRoot = Split-Path -Parent $here
$deckDir = Join-Path $repoRoot 'streamer-mode\deck'

$profilesDir = Join-Path $env:APPDATA 'HotSpot\StreamDock\profiles'
if ([string]::IsNullOrWhiteSpace($Target)) {
    $target = Join-Path $profilesDir "$ProfileId.sdProfile"
} else {
    $target = $Target
    $profilesDir = Split-Path -Parent $Target
}

Write-Host ''
Write-Host '  DikGun Stream profile installer' -ForegroundColor Cyan
Write-Host '  ==============================='
Write-Host ''
Write-Host "  repo root : $repoRoot"
Write-Host "  deck dir  : $deckDir"
Write-Host "  target    : $target"
Write-Host ''

if (-not (Test-Path $source)) {
    Write-Host "  profile not found: $source" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $deckDir)) {
    Write-Host "  warning: deck scripts missing at $deckDir" -ForegroundColor Yellow
}

if ($WhatIf) { Write-Host '  -WhatIf: nothing written' -ForegroundColor Yellow; Write-Host '' }

# ------------------------------------------------------------------ backup
if ((Test-Path $target) -and -not $WhatIf) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = Join-Path $profilesDir "$ProfileId.backup-$stamp.sdProfile"
    Copy-Item $target $backup -Recurse -Force
    Write-Host "  backed up existing profile -> $(Split-Path -Leaf $backup)" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------ copy
if (-not $WhatIf) {
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item (Join-Path $source '*') $target -Recurse -Force
    Write-Host '  profile copied' -ForegroundColor Green
}

# ------------------------------------------------------------------ rewrite
# Walking the parsed JSON is far more reliable than regexing raw text: the
# backslashes are escaped, which makes any text substitution fragile.
function Rewrite-Manifest([string]$path) {
    $json = Get-Content $path -Raw | ConvertFrom-Json
    if (-not $json.Actions) { return 0 }

    $changed = 0
    foreach ($slot in $json.Actions.PSObject.Properties) {
        $action = $slot.Value
        if (-not $action.Settings) { continue }
        $p = $action.Settings.path
        if ([string]::IsNullOrWhiteSpace($p)) { continue }

        $newPath = $p
        if ($p -like '*__REPO__*') {
            $newPath = $p -replace '^__REPO__', $repoRoot
        }
        elseif ($p -match '(?i)streamer-mode\\deck\\') {
            $leaf = Split-Path $p -Leaf
            $newPath = Join-Path $deckDir $leaf
        }

        if ($newPath -ne $p) {
            $action.Settings.path = $newPath
            $changed++
            Write-Host ("    {0,-6} {1,-14} -> {2}" -f $slot.Name, $action.Name, $newPath) -ForegroundColor DarkGray
        }
    }

    if ($changed -gt 0 -and -not $WhatIf) {
        # NOT Set-Content -Encoding UTF8: in Windows PowerShell 5.1 that writes a
        # BOM, and the JSON parser rejects a BOM-prefixed manifest, which would
        # break every button in the profile.
        $text = $json | ConvertTo-Json -Depth 100
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($path, $text, $utf8NoBom)
    }
    return $changed
}

Write-Host ''
Write-Host '  rewriting button paths:' -ForegroundColor Cyan
$total = 0
foreach ($man in Get-ChildItem $target -Recurse -Filter 'manifest.json' -File -ErrorAction SilentlyContinue) {
    $n = Rewrite-Manifest $man.FullName
    if ($n -gt 0) {
        Write-Host "    $($man.FullName.Replace($target,'')) : $n button(s)" -ForegroundColor DarkGray
    }
    $total += $n
}

Write-Host ''
Write-Host "  paths rewritten: $total" -ForegroundColor Green
Write-Host ''
Write-Host '  Next:'
Write-Host '    1. Restart Stream Dock, or switch away and back to the profile'
Write-Host '    2. The BOT button is on the ADMIN page, row 3 middle'
Write-Host ''
