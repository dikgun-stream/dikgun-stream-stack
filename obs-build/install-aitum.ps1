# install-aitum.ps1 -- MUST run elevated (writes to C:\Program Files\obs-studio)
#
# Removes StreamElements SE.Live and installs Aitum Vertical + Aitum Multistream.
# SE.Live files are MOVED to a backup folder, never deleted outright.

$ErrorActionPreference = 'Stop'

$obs    = 'C:\Program Files\obs-studio'
$stage  = Join-Path $PSScriptRoot 'aitum'
$backup = Join-Path $PSScriptRoot 'backup-se-live'

if (Get-Process obs64 -ErrorAction SilentlyContinue) {
    throw 'OBS is running. Close it before running this script.'
}

Write-Host '=== [1/3] Removing SE.Live ==='
New-Item -ItemType Directory -Path $backup -Force | Out-Null

$seFiles = @(
    "$obs\obs-plugins\64bit\obs-streamelements-core.dll"
    "$obs\obs-plugins\64bit\obs-streamelements-core.pdb"
    "$obs\obs-plugins\64bit\obs-streamelements-set-machine-config.exe"
)
foreach ($f in $seFiles) {
    if (Test-Path $f) {
        Move-Item -LiteralPath $f -Destination (Join-Path $backup (Split-Path $f -Leaf)) -Force
        Write-Host ("  removed {0}" -f (Split-Path $f -Leaf))
    } else {
        Write-Host ("  (absent) {0}" -f (Split-Path $f -Leaf))
    }
}

$seData = Join-Path $obs 'data\obs-plugins\obs-streamelements-core'
if (Test-Path $seData) {
    Move-Item -LiteralPath $seData -Destination (Join-Path $backup 'data-obs-streamelements-core') -Force
    Write-Host '  removed data\obs-plugins\obs-streamelements-core'
}

Write-Host ''
Write-Host '=== [2/3] Installing Aitum ==='
foreach ($pkg in @('vertical-canvas-windows', 'aitum-multistream-windows')) {
    $src = Join-Path $stage $pkg
    if (-not (Test-Path $src)) { throw "Staged package missing: $src" }

    Copy-Item -Path (Join-Path $src 'obs-plugins\64bit\*') -Destination (Join-Path $obs 'obs-plugins\64bit') -Force

    $extract = Join-Path $src 'data\obs-plugins'
    foreach ($d in (Get-ChildItem $extract -Directory)) {
        Copy-Item -LiteralPath $d.FullName -Destination (Join-Path $obs 'data\obs-plugins') -Recurse -Force
        Write-Host ("  installed {0}" -f $d.Name)
    }
}

Write-Host ''
Write-Host '=== [3/3] Result ==='
Get-ChildItem (Join-Path $obs 'obs-plugins\64bit') -Filter '*.dll' |
    Where-Object { $_.Name -match 'aitum|vertical-canvas|streamelements' } |
    Select-Object Name, @{n='KB';e={[math]::Round($_.Length/1KB)}} | Format-Table -AutoSize

Write-Host 'DONE'
