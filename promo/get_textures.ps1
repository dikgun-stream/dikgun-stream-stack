# Fetch the CC0 textures used by the Blender stingers.
#
#   powershell -File get_textures.ps1
#
# Source: Poly Haven, "Cracked Concrete" (https://polyhaven.com/a/cracked_concrete)
# Licence: CC0 1.0 - public domain, commercial use allowed, no attribution
# required. Credited anyway, above.
#
# The images are NOT committed to this repo: they are 7 MB of binaries that a
# one-line download reproduces exactly. blender_letters.py will warn and skip the
# crack if these are missing, so a clone still renders.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$base = 'https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/cracked_concrete'

$files = @{
    'cracked_diff_2k.jpg' = "$base/cracked_concrete_diff_2k.jpg"
    'cracked_disp_2k.jpg' = "$base/cracked_concrete_disp_2k.jpg"
    'cracked_nor_2k.jpg'  = "$base/cracked_concrete_nor_gl_2k.jpg"
}

foreach ($name in $files.Keys) {
    $out = Join-Path $here $name
    if (Test-Path $out) { Write-Host "  have $name"; continue }
    Write-Host "  fetching $name ..."
    Invoke-WebRequest $files[$name] -OutFile $out -UseBasicParsing -TimeoutSec 180
    Write-Host ("    {0} KB" -f [math]::Round((Get-Item $out).Length / 1KB))
}
Write-Host ''
Write-Host '  textures ready. Re-render the stingers with:'
Write-Host '    blender --background --python blender_letters.py -- --out stingers'
