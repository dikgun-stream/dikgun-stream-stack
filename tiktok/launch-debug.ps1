# Launch TikTok LIVE Studio with the debug port open.
#
# Why: the app talks to its native service over an internal RPC transport
# (@bytegecko/node-client). Rather than reimplement that transport, we drive the
# app's OWN JavaScript through the Chrome DevTools Protocol, which requires the
# process to be launched with --remote-debugging-port.
#
# Elevation: LIVE Studio normally runs elevated (its game capture needs it), and
# an unelevated process cannot stop or replace it. So this script re-launches
# itself elevated (one UAC prompt) when it is not already an administrator.
#
# Usage:
#   powershell -File launch-debug.ps1             # relaunch with CDP, graceful stop first
#   powershell -File launch-debug.ps1 -Force      # force-stop first
#   powershell -File launch-debug.ps1 -NoRestart  # only start (assumes app closed)

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$NoRestart,
    [switch]$Elevated,
    [int]$Port = 9222
)

$ErrorActionPreference = 'Stop'

function Assert-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---- re-launch elevated -----------------------------------------------------
if (-not (Assert-Elevated)) {
    Write-Host "  not elevated - asking for elevation (one UAC prompt) ..." -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Port', $Port)
    if ($Force) { $argList += '-Force' }
    if ($NoRestart) { $argList += '-NoRestart' }
    # keep the elevated window open long enough to read any error
    $argList += @('-Elevated')
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs
    Write-Host "  elevated instance launched in a new window."
    Write-Host "  accept the UAC prompt; it will stop/start LIVE Studio with CDP on $Port."
    exit 0
}

$exe = 'C:\Program Files\TikTok LIVE Studio\1.40.2\TikTok LIVE Studio.exe'
if (-not (Test-Path $exe)) { throw "not found: $exe" }

$running = Get-Process -Name 'TikTok LIVE Studio' -ErrorAction SilentlyContinue
if ($running -and -not $NoRestart) {
    Write-Host "  stopping LIVE Studio ($($running.Count) process(es)) ..."
    # stop children first, then the root, so the tree cannot respawn
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'TikTok LIVE Studio.exe'" |
        Sort-Object { $_.ParentProcessId -eq 0 }
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
    }
    Start-Sleep -Seconds 4
    $left = Get-Process -Name 'TikTok LIVE Studio' -ErrorAction SilentlyContinue
    if ($left) {
        & taskkill /F /T /IM "TikTok LIVE Studio.exe" 2>&1 | Out-Null
        Start-Sleep -Seconds 3
    }
    if (Get-Process -Name 'TikTok LIVE Studio' -ErrorAction SilentlyContinue) {
        Write-Host "  could not stop it. Close it from the app (File > Quit / tray), then re-run." -ForegroundColor Red
        Read-Host "  press Enter to close"
        exit 1
    }
}

# ---- is the port already taken? --------------------------------------------
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { throw "port $Port is already in use (pid $($busy.OwningProcess))" }

Write-Host "  launching with --remote-debugging-port=$Port ..."
Start-Process -FilePath $exe -ArgumentList @(
    "--remote-debugging-port=$Port",
    '--remote-allow-origins=*'
) -WorkingDirectory (Split-Path $exe -Parent)

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $v = Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
        Write-Host "  CDP up: $($v.Browser)" -ForegroundColor Green
        Write-Host "  next: python probe_cdp.py"
        if ($Elevated) { Read-Host "  press Enter to close" }
        exit 0
    } catch { }
}

Write-Host "  CDP did not come up on $Port. The build may ignore the flag." -ForegroundColor Red
if ($Elevated) { Read-Host "  press Enter to close" }
exit 2
