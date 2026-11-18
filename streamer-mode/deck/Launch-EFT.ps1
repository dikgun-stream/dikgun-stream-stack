# Launch Escape From Tarkov: open the BSG launcher AND press Play.
#
# Why this is not a simple Start-Process:
#   * EscapeFromTarkov.exe cannot be launched directly -- verified, it opens a
#     window titled "Fatal error". The launcher must authenticate first.
#   * The launcher is CefSharp, so UI Automation sees nothing (8 nodes, no
#     buttons) and --force-renderer-accessibility does not change that.
#   * So the only path is a real mouse click on the Play button.
#
# A blind click is unsafe: that corner shows Play / Stop / login / update
# depending on state. So the click is gated on a picture match of the button
# region (eft_play.py) against a reference capture. No match -> no click.

param([switch]$DryRun, [switch]$Silent, [int]$PlayTimeoutSec = 90)

$ErrorActionPreference = 'Stop'
$log = Join-Path $env:LOCALAPPDATA 'StreamerMode\launcher.log'
New-Item -ItemType Directory -Path (Split-Path $log) -Force | Out-Null
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'

function Say([string]$m) {
    $line = "{0}  [eft] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding utf8
    if (-not $Silent) { Write-Host $line }
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class X {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
try { [void][X]::SetProcessDPIAware() } catch { }

$LAUNCHER = 'C:\Battlestate Games\BsgLauncher\BsgLauncher.exe'
$ASSETS   = Join-Path (Split-Path $here) 'assets\eft-play-ref.png'
$SHOT     = Join-Path $env:TEMP 'eft-launcher.png'

function Get-LauncherWindow {
    Get-Process -Name BsgLauncher -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } |
        Select-Object -First 1
}

# --- already playing?
if (Get-Process -Name EscapeFromTarkov -ErrorAction SilentlyContinue) {
    Say 'EFT already running - nothing to do'; exit 0
}

# --- 1. make sure the launcher is open
$proc = Get-LauncherWindow
if (-not $proc) {
    if (-not (Test-Path $LAUNCHER)) { Say "FAILED: launcher missing at $LAUNCHER"; exit 1 }
    if ($DryRun) { Say "DRY   would launch $LAUNCHER"; exit 0 }
    Start-Process -FilePath $LAUNCHER -WorkingDirectory (Split-Path $LAUNCHER)
    Say 'started BsgLauncher'
    $deadline = (Get-Date).AddSeconds(60)
    do { Start-Sleep -Milliseconds 500; $proc = Get-LauncherWindow } while (-not $proc -and (Get-Date) -lt $deadline)
    if (-not $proc) { Say 'FAILED: launcher window did not appear within 60s'; exit 1 }
    Say "launcher window up (pid $($proc.Id))"
} else {
    Say "launcher already running (pid $($proc.Id))"
}

if ($DryRun) { Say 'DRY   would check for the Play button and click it'; exit 0 }

if (-not (Test-Path $py)) { Say "FAILED: python not found at $py"; exit 1 }
if (-not (Test-Path $ASSETS)) { Say "FAILED: reference image missing at $ASSETS"; exit 1 }

# --- 2. wait for the Play button, then click it
Add-Type -AssemblyName System.Drawing
$deadline = (Get-Date).AddSeconds($PlayTimeoutSec)
$clicked = $false
$lastScore = $null

while (-not $clicked -and (Get-Date) -lt $deadline) {
    $h = $proc.MainWindowHandle
    if ([X]::IsIconic($h)) { [void][X]::ShowWindow($h, 9) }
    [void][X]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 600

    $r = New-Object X+RECT
    [void][X]::GetWindowRect($h, [ref]$r)
    $w = $r.R - $r.L; $hh = $r.B - $r.T
    if ($w -le 0 -or $hh -le 0) { Start-Sleep -Seconds 1; continue }

    $bmp = New-Object System.Drawing.Bitmap $w, $hh
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $hh))
    $bmp.Save($SHOT, [System.Drawing.Imaging.ImageFormat]::Png)
    $gfx.Dispose(); $bmp.Dispose()

    $raw = & $py (Join-Path $here 'eft_play.py') $SHOT $r.L $r.T $ASSETS 2>&1
    try { $res = $raw | ConvertFrom-Json } catch { Say "matcher error: $raw"; break }
    $lastScore = $res.score
    Say ("play-button check: match={0} score={1} luma={2}" -f $res.match, $res.score, $res.luma)

    if ($res.match) {
        [void][X]::SetCursorPos($res.x, $res.y)
        Start-Sleep -Milliseconds 250
        [X]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # LEFTDOWN
        Start-Sleep -Milliseconds 90
        [X]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # LEFTUP
        Say ("clicked Play at ({0},{1})" -f $res.x, $res.y)
        $clicked = $true
    } else {
        Start-Sleep -Seconds 2
    }
}

if (-not $clicked) {
    Say ("FAILED: Play button never appeared (last score {0}) - launcher may need login or an update" -f $lastScore)
    exit 1
}

# --- 3. wait for the game itself
$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 1
    $game = Get-Process -Name EscapeFromTarkov -ErrorAction SilentlyContinue
} while (-not $game -and (Get-Date) -lt $deadline)

if ($game) {
    Say "OK: EscapeFromTarkov.exe running (pid $($game[0].Id))"
    exit 0
}
Say 'FAILED: game did not start within 120s of the click'
exit 1
