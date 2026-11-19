# Launch Escape From Tarkov: open the BSG launcher AND press Play.
#
# Why this is not a simple Start-Process:
#   * EscapeFromTarkov.exe cannot be launched directly -- verified, it opens a
#     window titled "Fatal error". The launcher must authenticate first.
#   * The launcher is CefSharp, so UI Automation sees nothing (8 nodes, no
#     buttons) and --force-renderer-accessibility does not change that.
#   * So the only path is a real mouse click on the Play button, gated on an
#     image match so we never click blind.
#
# Preconditions are checked BEFORE acting, because the failure mode that matters
# is "the launcher is already running": it may have minimised to the tray, in
# which case a naive "does a window exist?" check decides it is not running,
# spawns a second instance, and the second instance silently exits.

param([switch]$DryRun, [switch]$Silent, [int]$PlayTimeoutSec = 90, [int]$MaxClicks = 3)

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
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
}
"@
try { [void][X]::SetProcessDPIAware() } catch { }

$LAUNCHER = 'C:\Battlestate Games\BsgLauncher\BsgLauncher.exe'
$ASSETS   = Join-Path (Split-Path $here) 'assets\eft-play-ref.png'
$SHOT     = Join-Path $env:TEMP 'eft-launcher.png'

# ---------------------------------------------------------------- preconditions
$game = Get-Process -Name EscapeFromTarkov -ErrorAction SilentlyContinue
if ($game) { Say "EFT already running (pid $($game[0].Id)) - nothing to do"; exit 0 }

function Get-LauncherWindow {
    # A launcher can be alive with no window (minimised to tray). Both states
    # matter: the window is what we click, the process is what we must not clone.
    $p = Get-Process -Name BsgLauncher -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } |
        Select-Object -First 1
    if ($p) { $p.Refresh() }
    return $p
}

$launcherProc = Get-Process -Name BsgLauncher -ErrorAction SilentlyContinue
Say ("preconditions: launcher processes={0}, with window={1}" -f `
    (@($launcherProc).Count), (@(Get-LauncherWindow).Count))

# ------------------------------------------------------------- ensure it is open
$win = Get-LauncherWindow
if (-not $win) {
    if ($launcherProc) {
        # Running but windowless: give it a chance to paint before spawning another.
        Say 'launcher process exists but has no window - waiting (not relaunching)'
        $deadline = (Get-Date).AddSeconds(20)
        do { Start-Sleep -Milliseconds 500; $win = Get-LauncherWindow } while (-not $win -and (Get-Date) -lt $deadline)
    }
    if (-not $win) {
        if ($DryRun) { Say "DRY   would launch $LAUNCHER"; exit 0 }
        Start-Process -FilePath $LAUNCHER -WorkingDirectory (Split-Path $LAUNCHER)
        Say 'started BsgLauncher'
        $deadline = (Get-Date).AddSeconds(60)
        do { Start-Sleep -Milliseconds 500; $win = Get-LauncherWindow } while (-not $win -and (Get-Date) -lt $deadline)
    }
}
if (-not $win) { Say 'FAILED: no launcher window appeared within 60s'; exit 1 }
Say "launcher window ready (pid $($win.Id))"

if ($DryRun) { Say 'DRY   would check for the Play button and click it'; exit 0 }
if (-not (Test-Path $py)) { Say "FAILED: python not found at $py"; exit 1 }
if (-not (Test-Path $ASSETS)) { Say "FAILED: reference image missing at $ASSETS"; exit 1 }

function Set-Foreground([IntPtr]$H) {
    # Plain SetForegroundWindow is refused unless we own the foreground or the
    # user just interacted. Attaching to the foreground thread makes it stick.
    if ([X]::IsIconic($H)) { [void][X]::ShowWindow($H, 9) }
    $fg = [X]::GetForegroundWindow()
    if ($fg -eq $H) { return $true }
    $fgTid = [X]::GetWindowThreadProcessId($fg, [IntPtr]::Zero)
    $myTid = [X]::GetCurrentThreadId()
    $attached = $false
    if ($fgTid -ne $myTid -and $fgTid -ne 0) { $attached = [X]::AttachThreadInput($myTid, $fgTid, $true) }
    try {
        [void][X]::BringWindowToTop($H)
        [void][X]::SetForegroundWindow($H)
    } finally {
        if ($attached) { [void][X]::AttachThreadInput($myTid, $fgTid, $false) }
    }
    Start-Sleep -Milliseconds 200
    return ([X]::GetForegroundWindow() -eq $H)
}

# --------------------------------------------------- look for Play, then click it
Add-Type -AssemblyName System.Drawing
$clicked = 0
$deadline = (Get-Date).AddSeconds($PlayTimeoutSec)
$lastScore = $null

while ($clicked -lt $MaxClicks -and (Get-Date) -lt $deadline) {
    # Re-resolve every pass: the launcher reassigns its window on restore.
    $win = Get-LauncherWindow
    if (-not $win) { Say 'launcher window disappeared - aborting'; break }
    $h = $win.MainWindowHandle

    $fgOk = Set-Foreground $h
    if (-not $fgOk) { Say 'warning: could not bring the launcher to the foreground' }
    Start-Sleep -Milliseconds 500

    # CopyFromScreen grabs whatever is on top at that rectangle, so being truly
    # foreground is a correctness requirement, not a nicety.
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
        Start-Sleep -Milliseconds 200
        [X]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 90
        [X]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
        $clicked++
        Say ("clicked Play at ({0},{1})  [attempt {2}/{3}]" -f $res.x, $res.y, $clicked, $MaxClicks)

        # Verify before deciding to click again; the launcher takes a moment.
        $wait = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Seconds 1
            $game = Get-Process -Name EscapeFromTarkov -ErrorAction SilentlyContinue
        } while (-not $game -and (Get-Date) -lt $wait)
        if ($game) { Say "OK: EscapeFromTarkov.exe running (pid $($game[0].Id))"; exit 0 }
        Say 'click did not start the game - retrying'
    } else {
        Start-Sleep -Seconds 2
    }
}

if ($clicked -eq 0) {
    Say ("FAILED: Play button never matched (last score {0}) - launcher may need login or an update" -f $lastScore)
    exit 1
}
Say ("FAILED: clicked {0} time(s) but the game never started" -f $clicked)
exit 1
