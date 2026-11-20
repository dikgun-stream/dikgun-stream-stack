# Control the BenQ ZOWIE XL2540 over DDC/CI.
#
#   powershell -File benq-display.ps1 -Status
#   powershell -File benq-display.ps1 -Mode 4
#   powershell -File benq-display.ps1 -Brightness 20
#   powershell -File benq-display.ps1 -Preset night
#   powershell -File benq-display.ps1 -Identify        step through the modes
#
# DDC/CI talks to the monitor over the video cable, so this changes what the
# monitor shows - it does NOT affect what OBS captures. Safe to run mid-stream.
#
# Presets live in benq-presets.json next to this script. "night" and "day" are
# only meaningful once the mode numbers are known: the XL2540 exposes four
# picture modes over DDC (04, 05, 08, 0B) and which one is which is not
# documented anywhere useful, so -Identify exists to find out.

[CmdletBinding()]
param(
    [switch]$Status,
    [int]$Mode,
    [int]$Brightness,
    [int]$Contrast,
    [string]$Preset,
    [switch]$Identify
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$presetFile = Join-Path $here 'benq-presets.json'
$csPath = Join-Path $here 'lib\Ddc.cs'

# VCP codes
$VCP = @{ brightness = 0x10; contrast = 0x12; mode = 0x14 }

if (-not ('Ddc' -as [type])) {
    if (-not (Test-Path $csPath)) {
        Write-Host "  helper not found: $csPath" -ForegroundColor Red
        Write-Host "  (Ddc.cs implements the dxva2 DDC/CI calls)"
        exit 1
    }
    Add-Type -TypeDefinition (Get-Content $csPath -Raw) -Language CSharp
}

function Get-Zowie {
    $mons = [Ddc]::Enumerate()
    $byBounds = $mons | Where-Object { $_.Bounds.left -eq 0 -and $_.Bounds.top -eq 0 }
    if ($byBounds) { return $byBounds }
    foreach ($m in $mons) {
        $c = [Ddc]::Capabilities($m.Handle)
        if ($c -match 'XL2540|ZOWIE') { return $m }
    }
    return $null
}

$z = Get-Zowie
if (-not $z) {
    Write-Host '  ZOWIE not found. Is DDC/CI enabled in the monitor OSD?' -ForegroundColor Red
    exit 1
}

function Read-Vcp([int]$code) {
    $cur = 0; $max = 0
    $ok = [Ddc]::TryGetVcp($z.Handle, [byte]$code, [ref]$cur, [ref]$max)
    if (-not $ok) { return $null }
    return [pscustomobject]@{ Current = $cur; Max = $max }
}

function Write-Vcp([int]$code, [int]$value) {
    return [Ddc]::SetVcp($z.Handle, [byte]$code, [uint32]$value)
}

function Show-Status {
    $b = Read-Vcp $VCP.brightness
    $c = Read-Vcp $VCP.contrast
    $m = Read-Vcp $VCP.mode
    Write-Host ''
    Write-Host '  BenQ ZOWIE XL2540' -ForegroundColor Cyan
    Write-Host '  -----------------'
    Write-Host ("  brightness : {0}" -f $b.Current)
    Write-Host ("  contrast   : {0}" -f $c.Current)
    Write-Host ("  picture mode: {0}   (available: 4, 5, 8, 11 = 0x04,0x05,0x08,0x0B)" -f $m.Current)
    if (Test-Path $presetFile) {
        $p = Get-Content $presetFile -Raw | ConvertFrom-Json
        Write-Host '  presets    :'
        foreach ($k in $p.PSObject.Properties.Name) {
            $v = $p.$k
            Write-Host ("     {0,-8} mode={1,-3} brightness={2,-4} contrast={3}" -f $k, $v.mode, $v.brightness, $v.contrast)
        }
    }
    Write-Host ''
}

function Apply-Preset([string]$name) {
    if (-not (Test-Path $presetFile)) {
        Write-Host "  no preset file at $presetFile" -ForegroundColor Red
        return
    }
    $p = Get-Content $presetFile -Raw | ConvertFrom-Json
    $v = $p.$name
    if (-not $v) {
        Write-Host "  unknown preset '$name'. Available: $($p.PSObject.Properties.Name -join ', ')" -ForegroundColor Red
        return
    }
    if ($null -ne $v.contrast) { [void](Write-Vcp $VCP.contrast $v.contrast) }
    if ($null -ne $v.brightness) { [void](Write-Vcp $VCP.brightness $v.brightness) }
    if ($null -ne $v.mode) { [void](Write-Vcp $VCP.mode $v.mode) }
    Start-Sleep -Milliseconds 400
    Write-Host "  applied preset '$name'" -ForegroundColor Green
    Show-Status
}

# --------------------------------------------------------------------- main
if ($Identify) {
    Write-Host ''
    Write-Host '  Stepping through the four DDC picture modes, 3s each.' -ForegroundColor Cyan
    Write-Host '  Note which number looks like your night profile, then set it in'
    Write-Host '  benq-presets.json.' -ForegroundColor Cyan
    foreach ($code in 4, 5, 8, 11) {
        [void](Write-Vcp $VCP.mode $code)
        Write-Host ("  mode {0} (0x{0:X2})" -f $code) -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
    Show-Status
}
elseif ($Preset) { Apply-Preset $Preset }
elseif ($PSBoundParameters.ContainsKey('Mode') -or
         $PSBoundParameters.ContainsKey('Brightness') -or
         $PSBoundParameters.ContainsKey('Contrast')) {
    if ($PSBoundParameters.ContainsKey('Contrast'))   { [void](Write-Vcp $VCP.contrast $Contrast) }
    if ($PSBoundParameters.ContainsKey('Brightness')) { [void](Write-Vcp $VCP.brightness $Brightness) }
    if ($PSBoundParameters.ContainsKey('Mode'))       { [void](Write-Vcp $VCP.mode $Mode) }
    Start-Sleep -Milliseconds 400
    Show-Status
}
else { Show-Status }

[Ddc]::Release($z.Handle)
