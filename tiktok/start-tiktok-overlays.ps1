# Start everything the TikTok overlays need, and repoint LIVE Studio at the new URL.
#
# Pipeline:
#   overlay server :7475  ->  read-only shim :7480  ->  cloudflared quick tunnel
#                         ->  LIVE Studio Link sources (both canvases)
#
# Why each piece exists:
#   * LIVE Studio rejects http://127.0.0.1 in its "Add link" source, so the
#     overlays have to arrive over public HTTPS.
#   * The shim forwards ONLY overlay pages / assets / overlay websocket. The
#     /api tree (panic, obs control, event posting) is not reachable through the
#     tunnel, so a public URL cannot be used to drive the stream.
#   * cloudflared quick tunnels get a NEW random hostname every run, and that
#     hostname is baked into every Link source - hence the automatic
#     --update-url step at the end.
#
# Usage:
#   powershell -File start-tiktok-overlays.ps1
#   powershell -File start-tiktok-overlays.ps1 -Pages "chat,alerts,topic,goals" -Tracks "0,1"

[CmdletBinding()]
param(
    [string]$Pages  = "chat,alerts,topic,goals",
    [string]$Tracks = "0,1",
    [int]$ShimPort  = 7480,
    [string]$OverlayPort = "7475"
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pybin = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $pybin)) { $pybin = (Get-Command python).Source }

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
$psi = @{ token = $token; url = $state.url; shimPort = $ShimPort }
$psi | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

# ------------------------------------------------------------ 1. overlay server
$up = $false
try { $null = Invoke-WebRequest "http://127.0.0.1:$OverlayPort/api/status" -TimeoutSec 3; $up = $true } catch { }
if (-not $up) {
    Write-Host "  overlay server not running - starting it on $OverlayPort"
    $srvDir = Join-Path (Split-Path -Parent $here) 'overlay-server'
    if (-not (Test-Path $srvDir)) { throw "overlay-server not found at $srvDir" }
    Start-Process -FilePath $pybin -WorkingDirectory $srvDir `
        -ArgumentList '-m','uvicorn','server:app','--host','127.0.0.1','--port',$OverlayPort,'--log-level','warning' `
        -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try { $null = Invoke-WebRequest "http://127.0.0.1:$OverlayPort/api/status" -TimeoutSec 3; $up = $true; break } catch { }
    }
    if (-not $up) { throw "overlay server did not come up on $OverlayPort" }
}
Write-Host "  overlay server : up on $OverlayPort"

# --------------------------------------------------------------------- 2. shim
$shimUp = $false
try { $null = Invoke-WebRequest "http://127.0.0.1:$ShimPort/o/$token/chat" -TimeoutSec 3; $shimUp = $true } catch { }
if (-not $shimUp) {
    Write-Host "  starting shim on $ShimPort"
    Start-Process -FilePath $pybin -WorkingDirectory $here `
        -ArgumentList 'public_shim.py','--port',$ShimPort,'--token',$token -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try { $null = Invoke-WebRequest "http://127.0.0.1:$ShimPort/o/$token/chat" -TimeoutSec 3; $shimUp = $true; break } catch { }
    }
    if (-not $shimUp) { throw "shim did not come up on $ShimPort" }
}
Write-Host "  shim           : up on $ShimPort (token $token)"

# ------------------------------------------------------------------- 3. tunnel
$tunnelLog = Join-Path $here 'tunnel.log'
Remove-Item $tunnelLog -Force -ErrorAction SilentlyContinue
$cf = Join-Path $here 'cloudflared.exe'
if (-not (Test-Path $cf)) { throw "cloudflared.exe missing - download it into $here" }
Write-Host "  starting cloudflared quick tunnel ..."
Start-Process -FilePath $cf -WorkingDirectory $here `
    -ArgumentList 'tunnel','--url',"http://127.0.0.1:$ShimPort",'--no-autoupdate','--logfile',$tunnelLog -WindowStyle Hidden

$url = $null
$deadline = (Get-Date).AddSeconds(75)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if (Test-Path $tunnelLog) {
        $m = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = [regex]::Match($m.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value; break }
    }
}
if (-not $url) { throw "could not read the tunnel URL from $tunnelLog" }
Write-Host "  tunnel         : $url"

# ------------------------------------------------- 4. point LIVE Studio at it
Write-Host "  updating LIVE Studio Link sources ..."
Push-Location $here
try {
    & $pybin livestudio_sync.py --list | Out-Null
    $existing = (& $pybin livestudio_sync.py --list) -join "`n"
    if ($existing -match 'Link source') {
        & $pybin livestudio_sync.py --update-url $url --token $token
    } else {
        & $pybin livestudio_sync.py --add --pages $Pages --tracks $Tracks --base $url --token $token
    }
} finally { Pop-Location }

$psi.url = $url
$psi | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

Write-Host ''
Write-Host '  ready. Overlay URLs:' -ForegroundColor Green
foreach ($p in ($Pages -split ',')) {
    Write-Host "    $url/o/$token/$p"
}
Write-Host ''
Write-Host "  These are what LIVE Studios Link sources point at." -ForegroundColor DarkGray
Write-Host '  Stopping cloudflared breaks them; re-run this script for a new tunnel.' -ForegroundColor DarkGray
