# Launch Deadlock the way Deadlock Mod Manager does it.
#
# DMM (GPL-3.0, Tauri) exposes NO command line, NO IPC and NO "launch on start"
# setting — but it does not need to be running while you play: it copies mod VPKs
# into game\citadel\addons and injects a SearchPaths block into gameinfo.gi.
#
# Its own launch path is simply:
#     steam.exe  "steam://run/1422450//-condebug"
# (verified in its source, steam_manager.rs, and in its log)
#
# So this script opens the manager, lets it re-assert the mod state, then fires
# the identical URI. Same result as pressing "Launch modded".

param([switch]$DryRun, [switch]$Silent)

$ErrorActionPreference = 'Stop'
$log = Join-Path $env:LOCALAPPDATA 'StreamerMode\launcher.log'
New-Item -ItemType Directory -Path (Split-Path $log) -Force | Out-Null
function Say([string]$m) {
    $line = "{0}  [deadlock] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding utf8
    if (-not $Silent) { Write-Host $line }
}

$DMM = 'C:\Users\JR\AppData\Local\Deadlock Mod Manager\deadlock-mod-manager.exe'
$GAMEINFO = 'Q:\SteamLibrary\steamapps\common\Deadlock\game\citadel\gameinfo.gi'
$URI = 'steam://run/1422450//-condebug'
$APP_ID = 1422450

# --- 0. already running? just focus it, do not launch a second copy
$game = Get-Process -Name deadlock -ErrorAction SilentlyContinue
if ($game) { Say 'Deadlock already running - nothing to do'; exit 0 }

# --- 1. preflight: are mods actually applied on disk?
# If a Steam verify/update replaced gameinfo.gi, a plain Steam launch runs the
# game UNMODDED with the VPKs sitting inert. That is the one real failure mode.
$modsOnDisk = $false
if (Test-Path $GAMEINFO) {
    $modsOnDisk = [bool](Select-String -LiteralPath $GAMEINFO -Pattern 'citadel/addons' -Quiet)
}
Say ("preflight: gameinfo.gi addons path present = {0}" -f $modsOnDisk)
if (-not $modsOnDisk) {
    Say 'WARNING: mod search paths missing - the manager needs to re-apply them'
}

# --- 2. preconditions, logged before anything is launched
# A running manager may have no window (tray/minimised), and Get-Process returns
# an ARRAY, so comparing .MainWindowHandle directly silently misbehaves. Resolve
# to a single windowed process first.
function Get-DmmWindow {
    Get-Process -Name 'deadlock-mod-manager' -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } |
        Select-Object -First 1
}

$dmmProcs = @(Get-Process -Name 'deadlock-mod-manager' -ErrorAction SilentlyContinue)
$dmmWin = Get-DmmWindow
Say ("preconditions: DMM processes={0}, with window={1}, game running=False" -f `
    $dmmProcs.Count, (@($dmmWin).Count))

if ($dmmProcs.Count -gt 0) {
    # Already open: reuse it. It re-asserts the mod state at startup, which has
    # already happened, so we do not need to wait for a window or relaunch.
    Say 'manager already running - reusing it'
} else {
    if ($DryRun) {
        Say 'DRY   would start Deadlock Mod Manager'
    } elseif (Test-Path $DMM) {
        Start-Process -FilePath $DMM
        Say 'started Deadlock Mod Manager'
        $deadline = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 300
            $dmmWin = Get-DmmWindow
        } while (-not $dmmWin -and (Get-Date) -lt $deadline)
        if ($dmmWin) {
            Say "manager window is up (pid $($dmmWin.Id))"
            Start-Sleep -Seconds 2      # let Auto-Reapply Mods finish writing
        } else {
            Say 'WARNING: manager window did not appear within 20s - continuing anyway'
        }
    } else {
        Say "WARNING: manager exe not found at $DMM"
    }
}

# --- 3. fire the URI
if ($DryRun) { Say "DRY   would launch $URI"; exit 0 }

Start-Process $URI
Say "launched $URI  (appid $APP_ID)"

# --- 4. confirm the game process actually appears
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Milliseconds 750
    $g = Get-Process -Name deadlock -ErrorAction SilentlyContinue
} while (-not $g -and (Get-Date) -lt $deadline)

if ($g) { Say "OK: deadlock.exe running (pid $($g[0].Id))" }
else { Say 'FAILED: deadlock.exe did not start within 60s (check Steam is not running elevated)' }
