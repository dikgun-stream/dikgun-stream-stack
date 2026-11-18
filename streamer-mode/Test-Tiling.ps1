<#
.SYNOPSIS
    Measures whether Streamer Mode actually tiles cleanly.

.DESCRIPTION
    Snapshots every visible top-level window, runs Streamer-Mode.ps1, snapshots
    again, then reports:
      * which windows appeared / disappeared
      * each tiled window's VISIBLE rect (DWM extended frame bounds) vs its target
      * pairwise overlap area between the tiled windows  <- the bug being chased

    Use -Mode to pick which layout to test. Pass -SkipApply to only inspect the
    current state without running the mode script.
#>
[CmdletBinding()]
param(
    [ValidateSet('stream', 'dev')][string]$Mode = 'stream',
    [switch]$SkipApply
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$modeScript = Join-Path $here 'Streamer-Mode.ps1'

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class T {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT r, int size);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextLength(IntPtr h);
}
'@

function Get-VisibleRect([IntPtr]$H) {
    $r = New-Object T+RECT
    $hr = [T]::DwmGetWindowAttribute($H, 9, [ref]$r, [Runtime.InteropServices.Marshal]::SizeOf($r))
    if ($hr -ne 0) { [void][T]::GetWindowRect($H, [ref]$r) }
    return $r
}

function Get-WindowSnapshot {
    $list = @()
    foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
        try { $p.Refresh() } catch { continue }
        $h = $p.MainWindowHandle
        if ($h -eq [IntPtr]::Zero) { continue }
        if (-not [T]::IsWindowVisible($h)) { continue }
        if (-not $p.MainWindowTitle) { continue }
        $r = Get-VisibleRect $h
        $list += [pscustomobject]@{
            Handle  = $h
            Proc    = $p.ProcessName
            Pid     = $p.Id
            Title   = $p.MainWindowTitle
            X       = $r.Left
            Y       = $r.Top
            W       = $r.Right - $r.Left
            H       = $r.Bottom - $r.Top
        }
    }
    return $list
}

function Show-Windows($snap, [string]$label) {
    Write-Host ""
    Write-Host "--- $label : $($snap.Count) window(s) ---"
    $snap | Sort-Object X, Y | ForEach-Object {
        Write-Host ("   {0,-22} {1,5},{2,-5} {3,4}x{4,-4} {5}" -f `
            $_.Proc, $_.X, $_.Y, $_.W, $_.H, $_.Title.Substring(0, [Math]::Min(38, $_.Title.Length)))
    }
}

function Get-Overlaps($a, $b) {
    $x1 = [Math]::Max($a.X, $b.X); $y1 = [Math]::Max($a.Y, $b.Y)
    $x2 = [Math]::Min($a.X + $a.W, $b.X + $b.W); $y2 = [Math]::Min($a.Y + $a.H, $b.Y + $b.H)
    if ($x2 -le $x1 -or $y2 -le $y1) { return 0 }
    return ($x2 - $x1) * ($y2 - $y1)
}

$before = Get-WindowSnapshot
Show-Windows $before 'BEFORE'

if (-not $SkipApply) {
    Write-Host ""
    Write-Host "=== running Streamer-Mode -Mode $Mode ==="
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode $Mode 2>&1 |
        ForEach-Object { Write-Host "    $_" }
}

Start-Sleep -Seconds 2
$after = Get-WindowSnapshot
Show-Windows $after 'AFTER'

Write-Host ""
Write-Host "=== appeared / disappeared ==="
$beforeIds = $before | ForEach-Object { "$($_.Proc)|$($_.Title)" }
$afterIds  = $after  | ForEach-Object { "$($_.Proc)|$($_.Title)" }
$new       = $after  | Where-Object { $beforeIds -notcontains "$($_.Proc)|$($_.Title)" }
$gone      = $before | Where-Object { $afterIds  -notcontains "$($_.Proc)|$($_.Title)" }
Write-Host "  new windows (+$($new.Count)):"
if ($new.Count)  { $new  | ForEach-Object { Write-Host ("     + {0,-20} {1}" -f $_.Proc, $_.Title) } } else { Write-Host '     none' }
Write-Host "  closed windows (-$($gone.Count)):"
if ($gone.Count) { $gone | ForEach-Object { Write-Host ("     - {0,-20} {1}" -f $_.Proc, $_.Title) } } else { Write-Host '     none' }

Write-Host ""
Write-Host "=== overlap check: TILED windows only ==="
$tiledNames = @('obs64','Stream Dock AJAZZ','Streamer.bot','DeepSeek Harness')
$tiled = $after | Where-Object { $tiledNames -contains $_.Proc -or ($_.Proc -eq 'brave' -and $_.Title -match 'DikGun|Chat') }
$tiled | ForEach-Object { Write-Host ("   {0,-20} {1,5},{2,-5} {3,4}x{4,-4} {5}" -f $_.Proc, $_.X, $_.Y, $_.W, $_.H, $_.Title) }

$bad = 0
for ($i = 0; $i -lt $tiled.Count; $i++) {
    for ($j = $i + 1; $j -lt $tiled.Count; $j++) {
        $area = Get-Overlaps $tiled[$i] $tiled[$j]
        if ($area -gt 0) {
            $bad++
            Write-Host ("   OVERLAP {0:N0} px² : {1} <-> {2}" -f $area, $tiled[$i].Proc, $tiled[$j].Proc)
        }
    }
}
if ($bad -eq 0) { Write-Host '   none — clean tiling' }

Write-Host ""
Write-Host "=== foreign windows still on the stream monitor (should be 0 after a sweep) ==="
$foreign = $after | Where-Object {
    $_.X -gt 1900 -and ($tiledNames -notcontains $_.Proc) -and
    -not ($_.Proc -eq 'brave' -and $_.Title -match 'DikGun|Chat') -and
    $_.X -lt 3840
}
if ($foreign.Count) { $foreign | ForEach-Object { Write-Host ("   FOREIGN {0,-20} {1},{2} {3}x{4}" -f $_.Proc, $_.X, $_.Y, $_.W, $_.H) } }
else { Write-Host '   none' }

Write-Host ""
Write-Host "=== edge overflow (virtual desktop is 0,-900 .. 3840,1080) ==="
$overflow = 0
foreach ($w in $tiled) {
    if ($w.X -le -10000) { continue }   # minimised: parked at the sentinel position
    if (($w.X + $w.W) -gt 3840 -or ($w.Y + $w.H) -gt 1080 -or $w.X -lt 0 -or $w.Y -lt -900) {
        $overflow++
        Write-Host ("   OVERFLOW {0} spans ({1},{2})..({3},{4})" -f $w.Proc, $w.X, $w.Y, ($w.X + $w.W), ($w.Y + $w.H))
    }
}
if ($overflow -eq 0) { Write-Host '   none' }
