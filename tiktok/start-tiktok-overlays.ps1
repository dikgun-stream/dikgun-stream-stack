# Start everything the TikTok feed needs, and repoint LIVE Studio at the new URL.
#
# Pipeline (relay architecture):
#   OBS canvases --RTMP--> mediamtx relay --WebRTC--> shim :7480 --cloudflared--> LIVE Studio feeds
#
# Why each piece exists:
#   * LIVE Studio rejects http://127.0.0.1 in its Link sources, so the feed player
#     page has to arrive over public HTTPS.
#   * The shim exposes ONLY /o/<token>/<page>, /assets, /items, /ws and the WHEP
#     signalling proxy. The /api tree (panic, OBS control, event posting) is not
#     reachable through the tunnel.
#   * cloudflared quick tunnels get a NEW random hostname per run, and that
#     hostname is baked into the feed sources - hence the automatic repoint.
#
# NOTE: this no longer creates per-overlay Link sources. In the relay design OBS
# composites the overlays and LIVE Studio only needs the two feed sources.
#
# Usage:
#   powershell -File start-tiktok-overlays.ps1            # reuse a live tunnel if possible
#   powershell -File start-tiktok-overlays.ps1 -NewTunnel # force a fresh cloudflared

[CmdletBinding()]
param(
    [int]$ShimPort = 7480,
    [string]$OverlayPort = '7475',
    [switch]$NewTunnel
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pybin = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $pybin)) { $pybin = (Get-Command python).Source }

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { $null = Invoke-WebRequest $url -TimeoutSec 3 -UseBasicParsing; return $true } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

# ---------------------------------------------------------------- state file
$stateFile = Join-Path $here 'tiktok-overlays.json'
$state = @{ token = ''; url = ''; shimPort = $ShimPort }
if (Test-Path $stateFile) {
    try { $state = Get-Content $stateFile -Raw | ConvertFrom-Json } catch { }
}
$token = $state.token
if (-not $token) {
    $bytes = New-Object byte[] 9
    (New-Object Random).NextBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# ------------------------------------------------------------ 1. overlay server
if (-not (Wait-Http "http://127.0.0.1:$OverlayPort/api/status" 3)) {
    Write-Host "  overlay server not running - starting it on $OverlayPort"
    $srvDir = Join-Path (Split-Path -Parent $here) 'overlay-server'
    if (-not (Test-Path $srvDir)) { throw "overlay-server not found at $srvDir" }
    Start-Process -FilePath $pybin -WorkingDirectory $srvDir `
        -ArgumentList '-m','uvicorn','server:app','--host','127.0.0.1','--port',$OverlayPort,'--log-level','warning' `
        -WindowStyle Hidden
    if (-not (Wait-Http "http://127.0.0.1:$OverlayPort/api/status" 45)) {
        throw "overlay server did not come up on $OverlayPort"
    }
}
Write-Host "  overlay server : up on $OverlayPort"

# --------------------------------------------------------------------- 2. shim
if (-not (Wait-Http "http://127.0.0.1:$ShimPort/o/$token/chat" 3)) {
    Write-Host "  starting shim on $ShimPort"
    Start-Process -FilePath $pybin -WorkingDirectory $here `
        -ArgumentList 'public_shim.py','--port',$ShimPort,'--token',$token -WindowStyle Hidden
    if (-not (Wait-Http "http://127.0.0.1:$ShimPort/o/$token/chat" 30)) {
        throw "shim did not come up on $ShimPort"
    }
}
Write-Host "  shim           : up on $ShimPort (token $token)"

# ------------------------------------------------------------------- 3. tunnel
$reuse = $false
if (-not $NewTunnel -and $state.url) {
    # is the previously advertised tunnel still alive and pointing at this shim?
    $reuse = Wait-Http "$($state.url)/o/$token/chat" 6
    if ($reuse) { Write-Host "  tunnel         : reusing $($state.url)" }
}

$url = if ($reuse) { $state.url } else { $null }
if (-not $reuse) {
    $tunnelLog = Join-Path $here 'tunnel.log'
    Remove-Item $tunnelLog -Force -ErrorAction SilentlyContinue
    $cf = Join-Path $here 'cloudflared.exe'
    if (-not (Test-Path $cf)) { throw "cloudflared.exe missing - download it into $here" }
    Write-Host '  starting cloudflared quick tunnel ...'
    Start-Process -FilePath $cf -WorkingDirectory $here `
        -ArgumentList 'tunnel','--url',"http://127.0.0.1:$ShimPort",'--no-autoupdate','--logfile',$tunnelLog -WindowStyle Hidden

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (Test-Path $tunnelLog) {
            $m = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' |
                 Select-Object -First 1
            if ($m) { $url = [regex]::Match($m.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value; break }
        }
    }
    if (-not $url) { throw "could not read the tunnel URL from $tunnelLog (60s)" }
    Write-Host "  tunnel         : $url"
}

# persist before repointing, so the feed script can read it
# Write BOM-free: Set-Content -Encoding utf8 on PowerShell 5.1 adds a BOM, and
# json readers (ours and python's) reject it.
$json = @{ token = $token; url = $url; shimPort = $ShimPort } | ConvertTo-Json
[IO.File]::WriteAllText($stateFile, $json, (New-Object System.Text.UTF8Encoding($false)))

# --------------------------------------------- 4. repoint the LIVE Studio feeds
Write-Host '  repointing LIVE Studio feed sources ...'
$feedOut = Join-Path (Split-Path $here -Parent) 'StreamerMode\feed-repoint.log'
$feedErr = Join-Path (Split-Path $here -Parent) 'StreamerMode\feed-repoint.err.log'
$proc = Start-Process -FilePath $pybin -WorkingDirectory $here `
    -ArgumentList 'livestudio_feed.py' -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $feedOut -RedirectStandardError $feedErr
if (-not $proc.WaitForExit(90000)) {
    try { $proc.Kill() } catch { }
    Write-Host '  WARN  feed repoint still running after 90s - check StreamerMode\feed-repoint.log'
} else {
    $tail = (Get-Content $feedOut -Tail 4 -ErrorAction SilentlyContinue) -join ' / '
    Write-Host "  feed repoint   : rc=$($proc.ExitCode) $tail"
}

Write-Host ''
Write-Host '  ready.' -ForegroundColor Green
Write-Host "    feed pages : $url/o/$token/feed?path=portrait" -ForegroundColor DarkGray
Write-Host "                 $url/o/$token/feed?path=landscape" -ForegroundColor DarkGray
Write-Host '    stopping cloudflared breaks them; re-run this script for a new tunnel.' -ForegroundColor DarkGray
