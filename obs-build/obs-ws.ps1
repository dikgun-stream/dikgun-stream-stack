# obs-ws.ps1 -- minimal obs-websocket v5 client for PowerShell
#
# Usage:
#   . .\obs-ws.ps1
#   Connect-Obs
#   (Invoke-Obs 'GetSceneList').responseData
#
# Reads the websocket password from the OBS plugin config; never hardcode it.

$ErrorActionPreference = 'Stop'

function Get-ObsWsConfig {
    $p = Join-Path $env:APPDATA 'obs-studio\plugin_config\obs-websocket\config.json'
    if (-not (Test-Path $p)) { throw "obs-websocket config not found: $p" }
    Get-Content $p -Raw | ConvertFrom-Json
}

function Receive-ObsMessage {
    $buffer = [byte[]]::new(1048576)
    $sb = [System.Text.StringBuilder]::new()
    do {
        $cts = [System.Threading.CancellationTokenSource]::new(15000)
        try {
            $seg = [System.ArraySegment[byte]]::new($buffer)
            $res = $script:Ws.ReceiveAsync($seg, $cts.Token).GetAwaiter().GetResult()
        } catch {
            throw "Timed out waiting for OBS to respond."
        } finally {
            $cts.Dispose()
        }
        if ($res.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
            throw "WebSocket closed by OBS (code $($res.CloseStatus))."
        }
        [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $res.Count))
    } while (-not $res.EndOfMessage)
    $sb.ToString()
}

function Send-ObsPayload($obj) {
    $json = $obj | ConvertTo-Json -Depth 30 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $cts = [System.Threading.CancellationTokenSource]::new(15000)
    try {
        $seg = [System.ArraySegment[byte]]::new($bytes)
        [void]$script:Ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
    } finally { $cts.Dispose() }
}

function Connect-Obs {
    param([string]$Uri = 'ws://127.0.0.1:4455')

    $cfg = Get-ObsWsConfig
    $ws = [System.Net.WebSockets.ClientWebSocket]::new()
    $ws.ConnectAsync([Uri]$Uri, [System.Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $script:Ws = $ws

    $hello = (Receive-ObsMessage) | ConvertFrom-Json
    if ($hello.op -ne 0) { throw "Expected Hello (op 0), got op $($hello.op)" }

    $identify = @{ rpcVersion = 1; eventSubscriptions = 0 }
    if ($hello.d.authentication) {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $secret = [Convert]::ToBase64String(
                $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(
                    $cfg.server_password + $hello.d.authentication.salt)))
            $identify.authentication = [Convert]::ToBase64String(
                $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(
                    $secret + $hello.d.authentication.challenge)))
        } finally { $sha.Dispose() }
    }

    Send-ObsPayload @{ op = 1; d = $identify }

    while ($true) {
        $msg = (Receive-ObsMessage) | ConvertFrom-Json
        if ($msg.op -eq 2) { break }
        if ($msg.op -eq 5) { continue }
        if ($msg.op -eq 9) { throw "OBS rejected authentication: $($msg.d.error)" }
    }

    Write-Host "Connected to OBS (obs-websocket $($hello.d.obsWebSocketVersion))" -ForegroundColor Green
}

function Invoke-Obs {
    param(
        [Parameter(Mandatory)][string]$RequestType,
        [hashtable]$RequestData
    )

    $id = [guid]::NewGuid().ToString()
    $payload = @{ op = 6; d = @{ requestId = $id; requestType = $RequestType } }
    if ($RequestData) { $payload.d.requestData = $RequestData }
    Send-ObsPayload $payload

    while ($true) {
        $msg = (Receive-ObsMessage) | ConvertFrom-Json
        if ($msg.op -eq 5) { continue }
        if ($msg.op -eq 7 -and $msg.d.requestId -eq $id) { return $msg.d }
    }
}

function Close-Obs {
    if ($script:Ws) {
        try {
            $script:Ws.CloseAsync(
                [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, 'done',
                [System.Threading.CancellationToken]::None).GetAwaiter().GetResult()
        } catch { }
        $script:Ws.Dispose()
        $script:Ws = $null
    }
}
