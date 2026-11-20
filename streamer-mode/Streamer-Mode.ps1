<#
.SYNOPSIS
    Streamer Mode for DikGun. Puts the machine into a clean streaming desktop and
    tiles the streaming apps onto the Hisense monitor.

.DESCRIPTION
    -Mode stream  : hides desktop icons, auto-hides the taskbar, launches + tiles
                    OBS, dashboard, chat and Stream Dock on the Hisense.
    -Mode dev     : same tiling, but ALSO Streamer.bot + DeepSeek Harness, and
                    nothing is hidden (a dev box should stay usable).
    -Restore      : undo (taskbar + icons back to how they were).
    -DryRun       : print every planned rect / flag, touch nothing.

    Built on verified behaviour for Windows 11 build 26200 (25H2):
      * ABM_SETSTATE(3) flips taskbar auto-hide on all monitors instantly, with no
        explorer restart, and is exactly reversible.
      * Desktop icons are a ShowWindow on the SysListView32 named 'FolderView'.
        Non-persistent by design -- it self-heals on reboot / explorer restart.
      * All three monitors are 96 DPI, so pixel maths is exact. DPI awareness is
        still set first, because an unaware process gets virtualised coordinates.
      * The Hisense sits at Y = -17. Nothing here may assume non-negative coords.
#>

[CmdletBinding()]
param(
    [ValidateSet('stream', 'dev')]
    [string]$Mode = 'stream',
    [switch]$Restore,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------- setup
$StateDir = Join-Path $env:LOCALAPPDATA 'StreamerMode'
$StatePath = Join-Path $StateDir 'state.json'
$LogPath = Join-Path $StateDir 'last-run.log'
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

$script:Warnings = @()
function Write-Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Add-Content -Path $LogPath -Value $line -Encoding utf8
    Write-Host $line
}
function Warn([string]$msg) {
    $script:Warnings += $msg
    Write-Log "WARN  $msg"
}

# DPI awareness must be the first thing we do.
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class W
{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetProcessDPIAware();

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string title);

    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);

    // Visible bounds, excluding the invisible DWM resize border. Tiling with
    // GetWindowRect alone makes adjacent windows look overlapped by ~7-8px.
    [DllImport("dwmapi.dll")]
    public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attr, out RECT rect, int size);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct APPBARDATA {
        public int cbSize;
        public IntPtr hWnd;
        public uint uCallbackMessage;
        public uint uEdge;
        public RECT rc;
        public IntPtr lParam;
    }

    [DllImport("shell32.dll")]
    public static extern IntPtr SHAppBarMessage(uint msg, ref APPBARDATA data);
}
'@

try {
    if (-not [W]::SetProcessDpiAwarenessContext([IntPtr](-4))) { [W]::SetProcessDPIAware() | Out-Null }
} catch { [W]::SetProcessDPIAware() | Out-Null }

# ------------------------------------------------------------------- constants
# Hisense is DISPLAY1: 1920x1080 at (1920,-17). Full height because auto-hide is on.
$MON_X = 1920
$MON_Y = -17
$MON_W = 1920
$MON_H = 1080

$SWP_FLAGS = 0x0114 + 0x4000      # NOZORDER|NOACTIVATE|NOCOPYBITS + ASYNCWINDOWPOS
$ABM_SETSTATE = 0x0A
$ABS_AUTOHIDE = 3                 # ABS_AUTOHIDE(1) | ABS_ALWAYSONTOP(2)
$ABS_NORMAL = 2

$OBS_EXE = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe'
$OBS_DIR = 'C:\Program Files\obs-studio\bin\64bit'
$DOCK_EXE = 'C:\Program Files (x86)\Stream Dock AJAZZ Global\Stream Dock AJAZZ.exe'
$SB_EXE = "$env:USERPROFILE\Apps\Streamer.bot\Streamer.bot.exe"
$DSH_EXE = "$env:LOCALAPPDATA\Programs\DeepSeek Harness\DeepSeek Harness.exe"
$DASH_URL = 'http://127.0.0.1:7475/'
$CHAT_URL = 'http://127.0.0.1:7475/chatroom'

# ------------------------------------------------------------------- primitives
function Test-Port([string]$HostName = '127.0.0.1', [int]$Port, [int]$TimeoutMs = 250) {
    $c = [System.Net.Sockets.TcpClient]::new()
    try { return $c.ConnectAsync($HostName, $Port).Wait($TimeoutMs) } finally { $c.Dispose() }
}

function Get-AncestorPids {
    # Never close the process chain that is running this script -- if OBS or a
    # terminal or the harness spawned us, killing it would be self-destructive.
    $out = @()
    $cur = $PID
    for ($i = 0; $i -lt 10; $i++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $parent = [int]$proc.ParentProcessId
        if ($parent -le 0 -or $parent -eq $cur) { break }
        $out += $parent
        $cur = $parent
    }
    return $out
}

function Get-DesktopListView {
    $pm = [W]::FindWindow('Progman', 'Program Manager')
    if ($pm -ne [IntPtr]::Zero) {
        $dv = [W]::FindWindowEx($pm, [IntPtr]::Zero, 'SHELLDLL_DefView', $null)
        if ($dv -ne [IntPtr]::Zero) {
            $lv = [W]::FindWindowEx($dv, [IntPtr]::Zero, 'SysListView32', 'FolderView')
            if ($lv -ne [IntPtr]::Zero) { return $lv }
        }
    }
    # 24H2+ may parent SHELLDLL_DefView under WorkerW -- enumerate, never assume.
    $worker = [IntPtr]::Zero
    do {
        $worker = [W]::FindWindowEx([IntPtr]::Zero, $worker, 'WorkerW', $null)
        if ($worker -ne [IntPtr]::Zero) {
            $dv = [W]::FindWindowEx($worker, [IntPtr]::Zero, 'SHELLDLL_DefView', $null)
            if ($dv -ne [IntPtr]::Zero) {
                $lv = [W]::FindWindowEx($dv, [IntPtr]::Zero, 'SysListView32', 'FolderView')
                if ($lv -ne [IntPtr]::Zero) { return $lv }
            }
        }
    } while ($worker -ne [IntPtr]::Zero)
    return [IntPtr]::Zero
}

function Set-TaskbarAutohide([bool]$On) {
    $abd = New-Object W+APPBARDATA
    $abd.cbSize = [Runtime.InteropServices.Marshal]::SizeOf([type]'W+APPBARDATA')
    $abd.hWnd = [W]::FindWindow('Shell_TrayWnd', $null)
    $abd.lParam = [IntPtr]($(if ($On) { $ABS_AUTOHIDE } else { $ABS_NORMAL }))
    if ($DryRun) { Write-Log "DRY   would ABM_SETSTATE -> $($abd.lParam)"; return }
    [void][W]::SHAppBarMessage([uint32]$ABM_SETSTATE, [ref]$abd)
    Start-Sleep -Milliseconds 250
}

function Set-DesktopIcons([bool]$Visible) {
    $lv = Get-DesktopListView
    if ($lv -eq [IntPtr]::Zero) { Warn 'desktop icon window not found'; return }
    if ($DryRun) { Write-Log "DRY   would ShowWindow icons -> visible=$Visible"; return }
    [void][W]::ShowWindow($lv, $(if ($Visible) { 5 } else { 0 }))
}

function Wait-WindowHandle {
    param(
        [scriptblock]$Predicate,       # param($proc) -> bool
        [int]$TimeoutSec = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
            try { $p.Refresh() } catch { continue }
            if ($p.MainWindowHandle -eq [IntPtr]::Zero) { continue }
            if ($Predicate.Invoke($p)) { return $p.MainWindowHandle }
        }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return [IntPtr]::Zero
}

function Get-VisibleRect([IntPtr]$Handle) {
    # DWM reports the frame the user actually sees; GetWindowRect includes the
    # invisible ~7px resize border, which is what makes tiled windows LOOK
    # overlapped even when the maths is right.
    $r = New-Object W+RECT
    $hr = [W]::DwmGetWindowAttribute($Handle, 9, [ref]$r, [Runtime.InteropServices.Marshal]::SizeOf($r))
    if ($hr -ne 0) { [void][W]::GetWindowRect($Handle, [ref]$r) }
    return $r
}

function Place-Window {
    param([IntPtr]$Handle, [int]$X, [int]$Y, [int]$W, [int]$H, [string]$Label)
    if ($Handle -eq [IntPtr]::Zero) { Warn "$Label : no window handle"; return }
    if ($DryRun) { Write-Log ("DRY   {0,-14} -> ({1},{2}) {3}x{4}" -f $Label, $X, $Y, $W, $H); return }

    [void][W]::ShowWindow($Handle, 9)          # SW_RESTORE: maximised windows ignore resize
    Start-Sleep -Milliseconds 150

    # Border compensation: grow the requested window rect so the VISIBLE rect
    # lands exactly on the target tile.
    $vis = Get-VisibleRect $Handle
    $win = New-Object W+RECT
    [void][W]::GetWindowRect($Handle, [ref]$win)
    $dl = $win.Left - $vis.Left
    $dt = $win.Top - $vis.Top
    $dr = $win.Right - $vis.Right
    $db = $win.Bottom - $vis.Bottom

    $tx = $X + $dl
    $ty = $Y + $dt
    $tw = $W + $dr - $dl
    $th = $H + $db - $dt

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        [void][W]::SetWindowPos($Handle, [IntPtr]::Zero, $tx, $ty, $tw, $th, [uint32]$SWP_FLAGS)
        Start-Sleep -Milliseconds 300
        $v = Get-VisibleRect $Handle
        $dx = [Math]::Abs($v.Left - $X); $dy = [Math]::Abs($v.Top - $Y)
        $dw = [Math]::Abs(($v.Right - $v.Left) - $W); $dh = [Math]::Abs(($v.Bottom - $v.Top) - $H)
        if ($dx -le 2 -and $dy -le 2 -and $dw -le 2 -and $dh -le 2) {
            Write-Log ("OK    {0,-14} visible ({1},{2}) {3}x{4}  (frame +{5},+{6})" -f $Label, $X, $Y, $W, $H, $dl, $dt)
            return
        }
        if ($attempt -lt 3) { Start-Sleep -Milliseconds 250 }
    }
    Warn "$Label : PLACEMENT_FAILED visible=$($v.Left),$($v.Top) $($v.Right-$v.Left)x$($v.Bottom-$v.Top) want=$X,$Y ${W}x${H}"
}

function Start-IfNeeded {
    param([string]$Label, [string]$Exe, [string]$WorkDir, [string[]]$Arguments, [scriptblock]$Predicate)
    if (-not (Test-Path $Exe)) { Warn "$Label : exe not found ($Exe)"; return }
    $existing = Wait-WindowHandle -Predicate $Predicate -TimeoutSec 2
    if ($existing -ne [IntPtr]::Zero) { Write-Log "FOUND $Label already running"; return }
    if ($DryRun) { Write-Log "DRY   would launch $Label"; return }
    $params = @{ FilePath = $Exe }
    if ($WorkDir) { $params.WorkingDirectory = $WorkDir }
    if ($Arguments) { $params.ArgumentList = $Arguments }
    Start-Process @params | Out-Null
    Write-Log "START $Label"
}

function Get-BrowserExe {
    foreach ($c in @(
        @{ N = 'brave.exe';  P = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe' },
        @{ N = 'chrome.exe'; P = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe' },
        @{ N = 'msedge.exe'; P = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe' }
    )) {
        try { $v = (Get-ItemProperty $c.P -ErrorAction Stop).'(default)'; if ($v -and (Test-Path $v)) { return $v } } catch { }
    }
    return $null
}
$BrowserExe = Get-BrowserExe

# ---------------------------------------------------------------------- restore
if ($Restore) {
    Write-Log '=== RESTORE ==='
    $prior = $null
    if (Test-Path $StatePath) { try { $prior = Get-Content $StatePath -Raw | ConvertFrom-Json } catch { } }

    # default to a healthy normal state if we have no record
    $wantAutohide = $false
    $wantIcons = $true
    if ($prior) {
        if ($null -ne $prior.taskbar.autohideBefore) { $wantAutohide = [bool]$prior.taskbar.autohideBefore }
        if ($null -ne $prior.icons.wasVisible) { $wantIcons = [bool]$prior.icons.wasVisible }
    }
    Set-TaskbarAutohide (-not $wantAutohide)
    Set-TaskbarAutohide ($wantAutohide)     # settle to the recorded value
    Set-DesktopIcons $wantIcons
    if (-not $DryRun -and (Test-Path $StatePath)) { Remove-Item $StatePath -Force }
    Write-Log ("restored: autohide={0} iconsVisible={1}" -f $wantAutohide, $wantIcons)
    if ($script:Warnings.Count) { Write-Log ("warnings: " + ($script:Warnings -join '; ')) }
    return
}

# ------------------------------------------------------------------------ apply
Write-Log "=== STREAMER MODE ($Mode) ==="

# --- record prior state (write-ahead, so a crash is still recoverable)
$iconsLv = Get-DesktopListView
$iconsVisible = ($iconsLv -ne [IntPtr]::Zero) -and [W]::IsWindowVisible($iconsLv)

# Preserve the TRUE baseline across repeated runs. On a second run the desktop is
# already hidden, so naively recording "wasVisible = false" would make -Restore
# restore the machine to a hidden state instead of the original.
$priorBaseline = $null
if (Test-Path $StatePath) {
    try { $priorBaseline = Get-Content $StatePath -Raw | ConvertFrom-Json } catch { }
}
$baseIconsVisible = if ($priorBaseline -and $null -ne $priorBaseline.icons.wasVisible) {
    [bool]$priorBaseline.icons.wasVisible
} else { $iconsVisible }
$baseAutohide = if ($priorBaseline -and $null -ne $priorBaseline.taskbar.autohideBefore) {
    [bool]$priorBaseline.taskbar.autohideBefore
} else { $false }

$state = [ordered]@{
    sessionId   = [guid]::NewGuid().ToString()
    startedUtc  = (Get-Date).ToUniversalTime().ToString('o')
    mode        = $Mode
    icons       = @{ wasVisible = $baseIconsVisible }
    taskbar     = @{ autohideBefore = $baseAutohide; method = 'ABM_SETSTATE' }
    browser     = $BrowserExe
    warnings    = @()
}
if (-not $DryRun) { $state | ConvertTo-Json -Depth 6 | Set-Content $StatePath -Encoding utf8 }

# --- 1. taskbar auto-hide (global, instant, reversible)
if ($Mode -eq 'stream') {
    Set-TaskbarAutohide $true
} else {
    Write-Log 'dev mode: leaving taskbar alone'
}

# --- 2. serve the dashboard if it is not already up
if (-not (Test-Port -Port 7475)) {
    Write-Log 'dashboard server not listening - starting it'
    if (-not $DryRun) {
        $py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        $srv = 'C:\Users\JR\Documents\deepseek-harness\default-workspace\overlay-server'
        if (Test-Path $py) {
            Start-Process -FilePath $py `
                -ArgumentList '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '7475', '--log-level', 'warning' `
                -WorkingDirectory $srv -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $StateDir 'uvicorn.out.log') `
                -RedirectStandardError  (Join-Path $StateDir 'uvicorn.err.log')
        }
        $deadline = (Get-Date).AddSeconds(30)
        while (-not (Test-Port -Port 7475) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 400 }
    }
    if (Test-Port -Port 7475) { Write-Log 'dashboard server up' } else { Warn 'dashboard server did not come up' }
} else {
    Write-Log 'dashboard server already listening'
}

# --- 3. launch apps
$obsPred  = { param($p) $p.ProcessName -eq 'obs64' }
$dockPred = { param($p) $p.ProcessName -like 'Stream Dock AJAZZ*' }
$sbPred   = { param($p) $p.ProcessName -eq 'Streamer.bot' }
$dshPred  = { param($p) $p.ProcessName -eq 'DeepSeek Harness' }

Start-IfNeeded -Label 'OBS' -Exe $OBS_EXE -WorkDir $OBS_DIR `
    -Arguments @('--profile', 'Untitled', '--collection', 'DikGun') -Predicate $obsPred
Start-IfNeeded -Label 'Stream Dock' -Exe $DOCK_EXE -Predicate $dockPred

# browser tiles: separate profiles => separate processes => unambiguous handles
$browserJobs = @(
    @{ Key = 'dashboard'; Url = $DASH_URL; Profile = 'dashboard' }
    @{ Key = 'chat';      Url = $CHAT_URL; Profile = 'chat' }
)
foreach ($b in $browserJobs) {
    if (-not $BrowserExe) { Warn 'no Chromium browser found'; continue }
    $profileDir = Join-Path $StateDir ('browser\' + $b.Profile)
    $pred = {
        param($p)
        $p.ProcessName -in @('brave', 'chrome', 'msedge') -and $p.MainWindowTitle -notmatch 'DevTools'
    }
    $already = Get-CimInstance Win32_Process -Filter "Name='$([IO.Path]::GetFileName($BrowserExe))'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profileDir) } |
        Select-Object -First 1
    if ($already) {
        Write-Log "FOUND $($b.Key) browser already running"
    } elseif ($DryRun) {
        Write-Log "DRY   would launch $($b.Key) browser ($($b.Url))"
    } else {
        Start-Process -FilePath $BrowserExe -ArgumentList @(
            "--user-data-dir=$profileDir", '--profile-directory=Default', "--app=$($b.Url)",
            '--no-first-run', '--no-default-browser-check',
            '--disable-features=Translate', '--disable-session-crashed-bubble'
        ) | Out-Null
        Write-Log "START $($b.Key) browser"
    }
}

if ($Mode -eq 'dev') {
    Start-IfNeeded -Label 'DeepSeek Harness' -Exe $DSH_EXE -Predicate $dshPred
}

# Streamer.bot runs in BOTH modes: it sources the follow/sub alerts, so leaving
# it closed in stream mode silently breaks them. It is launched here but only
# tiled in dev mode -- in stream mode its window is minimised instead.
Start-IfNeeded -Label 'Streamer.bot' -Exe $SB_EXE -Predicate $sbPred

# --- 4. tile
$obsH = Wait-WindowHandle -Predicate $obsPred -TimeoutSec 45
$dockH = Wait-WindowHandle -Predicate $dockPred -TimeoutSec 30

function Get-BrowserWindow([string]$profileLeaf) {
    # In a dry run we never launched it, so do not sit here waiting for one.
    if ($DryRun) { return [IntPtr]::Zero }
    $full = Join-Path $StateDir ('browser\' + $profileLeaf)
    $deadline = (Get-Date).AddSeconds(20)
    do {
        $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($full) -and $_.CommandLine -notmatch '--type=' } |
            Select-Object -First 1
        if ($proc) {
            $gp = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
            if ($gp) { $gp.Refresh(); if ($gp.MainWindowHandle -ne [IntPtr]::Zero) { return $gp.MainWindowHandle } }
        }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return [IntPtr]::Zero
}

$dashH = Get-BrowserWindow 'dashboard'
$chatH = Get-BrowserWindow 'chat'
# A dry run never launches it, so do not sit waiting for a window that will not
# appear -- that added 30s of dead time to every dry run.
$sbH = if ($DryRun) { [IntPtr]::Zero } else { Wait-WindowHandle -Predicate $sbPred -TimeoutSec 30 }
$dshH = if ($Mode -eq 'dev' -and -not $DryRun) { Wait-WindowHandle -Predicate $dshPred -TimeoutSec 30 } else { [IntPtr]::Zero }

# Measured minimum window sizes on this machine (probe: request 300x300, read back).
# Tiles smaller than these are silently clamped by the app and then overlap.
#   OBS 1241x496 | Stream Dock 1202x832 | Streamer.bot 720x640
#   DeepSeek Harness 522x601 | Chromium app window 286x193
$HISENSE = @{ X = 1920; Y = -17;  W = 1920; H = 1080 }
$HP      = @{ X = 182;  Y = -900; W = 1600; H = 900 }
$ZOWIE   = @{ X = 0;    Y = 0;    W = 1920; H = 1080 }

$OBS_MIN_W = 1241
$DOCK_MIN_W = 1202
$DOCK_MIN_H = 832

# Hisense: OBS takes its minimum width, the rest of the column takes the browsers.
Place-Window $obsH $HISENSE.X $HISENSE.Y $OBS_MIN_W $HISENSE.H 'OBS'
$colX = $HISENSE.X + $OBS_MIN_W          # 3161
$colW = $HISENSE.W - $OBS_MIN_W          # 679
Place-Window $dashH $colX $HISENSE.Y              $colW 360 'dashboard'
Place-Window $chatH $colX ($HISENSE.Y + 360)      $colW 360 'chat'

# Stream Dock's 1202x832 floor makes it impossible to tile beside OBS, and it is
# a control surface rather than something you watch. Minimise it instead.
if ($dockH -ne [IntPtr]::Zero) {
    if ($DryRun) { Write-Log 'DRY   would minimise Stream Dock' }
    else { [void][W]::ShowWindow($dockH, 6); Write-Log 'MIN   stream dock' }
}

if ($Mode -eq 'dev') {
    # Dev tools go on the primary display; dev mode does not hide anything.
    Place-Window $sbH  $ZOWIE.X                $ZOWIE.Y 960 640 'Streamer.bot'
    Place-Window $dshH ($ZOWIE.X + 960)        $ZOWIE.Y 900 640 'DeepSeek Harness'
} elseif ($sbH -ne [IntPtr]::Zero) {
    # Stream mode: Streamer.bot is a background service, not something you
    # watch. Minimise so it cannot sit over the layout.
    if ($DryRun) { Write-Log 'DRY   would minimise Streamer.bot' }
    else { [void][W]::ShowWindow($sbH, 6); Write-Log 'MIN   Streamer.bot' }
}

# --- 4b. close everything that is not part of this mode
# Streamer mode should leave only the apps the layout actually uses. Anything
# else with a visible window is closed: graceful WM_CLOSE first, force after a
# grace period. Never touch the shell, or our own process ancestry.
$keepProcs = @('explorer','ShellExperienceHost','SearchHost','TextInputHost',
               'ApplicationFrameHost','StartMenuExperienceHost','LockApp',
               'dwm','RuntimeBroker','sihost','ctfmon',
               # Streamer.bot is a background service that sources the follow
               # alerts, not a window you look at. Killing it in stream mode
               # would silently break follow alerts. Remove this line if you
               # want it closed with everything else.
               'Streamer.bot')
$keepHandles = @($obsH, $dashH, $chatH, $dockH) | Where-Object { $_ -ne [IntPtr]::Zero }
if ($Mode -eq 'dev') {
    $keepHandles += @($sbH, $dshH) | Where-Object { $_ -ne [IntPtr]::Zero }
}

$ancestors = @(Get-AncestorPids) + $PID

# The overlay server is a python process with no window, but it is usually a
# child of a shell -- and here it is a background job of DeepSeek Harness. When
# stream mode force-closed the harness, the job object took the server with it
# and every overlay went blank. Protecting only the server is not enough: its
# parent has to survive too.
#
# Matching is deliberately narrow. An earlier version matched any process whose
# command line merely CONTAINED 'uvicorn server:app', which swept in the shells
# and the harness by accident.
$servicePatterns = @(
    'uvicorn\s+server:app',
    'server\.py',
    'discord-bot[\\/]bot\.py'
)
$servicePids = @()
try {
    foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if (-not $p.CommandLine) { continue }
        if ($p.Name -notmatch '^pythonw?\.exe$') { continue }
        foreach ($pat in $servicePatterns) {
            if ($p.CommandLine -match $pat) {
                $servicePids += [int]$p.ProcessId
                Write-Log "KEEP  $($p.Name) pid $($p.ProcessId)  (streaming service)"
                break
            }
        }
    }
} catch { }

# walk up from each service so its launcher is spared as well
foreach ($svcPid in $servicePids) {
    $cur = $svcPid
    for ($i = 0; $i -lt 8; $i++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $parent = [int]$proc.ParentProcessId
        if ($parent -le 0 -or $parent -eq $cur) { break }
        if ($ancestors -notcontains $parent) {
            $ancestors += $parent
            $pp = Get-Process -Id $parent -ErrorAction SilentlyContinue
            if ($pp) { Write-Log "KEEP  $($pp.ProcessName) pid $parent  (ancestor of the overlay server)" }
        }
        $cur = $parent
    }
}

foreach ($b in @('dashboard', 'chat')) {
    $dir = Join-Path $StateDir ('browser\' + $b)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($dir) } |
        ForEach-Object { $ancestors += [int]$_.ProcessId }
}

$toClose = @()
foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) {
    try { $proc.Refresh() } catch { continue }
    $h = $proc.MainWindowHandle
    if ($h -eq [IntPtr]::Zero) { continue }
    if ($keepHandles -contains $h) { continue }
    if ($keepProcs -contains $proc.ProcessName) { continue }
    if ($ancestors -contains $proc.Id) { continue }
    if (-not $proc.MainWindowTitle) { continue }
    if (-not [W]::IsWindowVisible($h)) { continue }
    $toClose += $proc
}

if ($DryRun) {
    Write-Log ("DRY   would close {0} program(s)" -f $toClose.Count)
    $toClose | ForEach-Object { Write-Log ("        - {0}  ({1})" -f $_.ProcessName, $_.MainWindowTitle) }
} elseif ($toClose.Count -eq 0) {
    Write-Log 'no foreign programs to close'
} else {
    foreach ($proc in $toClose) {
        $name = $proc.ProcessName
        $title = $proc.MainWindowTitle
        $closed = $false
        try {
            [void]$proc.CloseMainWindow()
            $closed = $proc.WaitForExit(4000)
        } catch { }
        if ($closed) {
            Write-Log "CLOSE $name (graceful)  $title"
        } else {
            try { Stop-Process -Id $proc.Id -Force; Write-Log "CLOSE $name (forced)  $title" }
            catch { Warn "could not close $name" }
        }
    }
}

# --- 5. icons last (least important, most likely to be re-shown)
if ($Mode -eq 'stream') { Set-DesktopIcons $false }
else { Write-Log 'dev mode: leaving desktop icons alone' }

$state.warnings = @($script:Warnings)
if (-not $DryRun) { $state | ConvertTo-Json -Depth 6 | Set-Content $StatePath -Encoding utf8 }

Write-Log ("done. {0} warning(s). Restore with: -Restore" -f $script:Warnings.Count)
