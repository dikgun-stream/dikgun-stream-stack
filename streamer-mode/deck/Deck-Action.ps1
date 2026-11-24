# Deck button helper. One entry point so each .bat is a one-liner.
param(
    [Parameter(Mandatory)][ValidateSet('golive', 'stop', 'panic', 'mic', 'hint', 'dashboard', 'chat',
                                      'kill', 'assist', 'damage', 'dlreset',
                                      'tiktok', 'tiktokgo', 'tiktokstop', 'tiktokstate', 'prep', 'streamall')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:7475'
$stateDir = Join-Path $env:LOCALAPPDATA 'StreamerMode'

function Post([string]$path, [string]$body = '{}') {
    Invoke-RestMethod -Uri "$base$path" -Method Post -ContentType 'application/json' `
        -Body $body -TimeoutSec 10 | Out-Null
}

try {
    switch ($Action) {
        # GO LIVE (homogenised): the server brings up the relay, the overlay
        # tunnel and LIVE Studio's feeds first, then starts Twitch + both YouTube
        # canvases + both TikTok feeds, and finally ARMS TikTok's pre-live panel
        # without broadcasting. 'tiktokgo' is the key that actually goes live there.
        'golive'    { Post '/api/live' '{"action":"start","prep":true,"tiktok":true}' }
        'streamall' { Post '/api/live' '{"action":"start","prep":true,"tiktok":true}' }
        'stop'      { Post '/api/live' '{"action":"stop","prep":false}' }
        'prep'      { Post '/api/prep' }
        'panic'  { Post '/api/panic' }
        'hint'   { Post '/api/hints/reveal' }

        'mic' {
            $audio = Invoke-RestMethod -Uri "$base/api/obs/audio" -TimeoutSec 10
            $mic = $audio.sources | Where-Object { $_.name -eq 'Mic/Aux' } | Select-Object -First 1
            if ($mic) {
                Post '/api/obs/audio' (@{ source = 'Mic/Aux'; muted = (-not $mic.muted) } | ConvertTo-Json)
            }
        }

        'dashboard' { }
        'chat'      { }

        # TikTok LIVE goes out through TikTok LIVE Studio (its own encoder), so
        # these hit the server's /api/tiktok/* endpoints, which drive the app over
        # CDP. 'tiktok' only opens the pre-live panel and is safe to press by
        # accident; 'tiktokgo' is the one that actually broadcasts.
        'tiktok'      { Post '/api/tiktok/live' '{"action":"panel"}' }
        'tiktokgo'    { Post '/api/tiktok/live' '{"action":"start","confirm":true}' }
        'tiktokstop'  { Post '/api/tiktok/live' '{"action":"stop","confirm":true}' }
        'tiktokstate' { $r = Invoke-RestMethod -Uri "$base/api/tiktok/state" -TimeoutSec 30
                        Write-Host ("  tiktok liveStatus={0} room={1} overlays={2}" -f `
                            $r.live.liveStatus, $r.live.roomId, $r.overlays.url) }

        # Deadlock panel counters -- manual on purpose: the game exposes no
        # passive feed for kills/assists/damage, so the streamer taps these.
        'kill'   { Post '/api/deadlock/increment' '{"field":"kills","by":1}' }
        'assist' { Post '/api/deadlock/increment' '{"field":"assists","by":1}' }
        'damage' { Post '/api/deadlock/increment' '{"field":"damage","by":500}' }
        'dlreset' { Post '/api/deadlock/reset' }
    }
} catch {
    # A dead server must not leave a console window hanging around.
    exit 1
}

# Browser tabs open their own window with the dedicated streaming profile.
if ($Action -in @('dashboard', 'chat')) {
    $browser = $null
    foreach ($c in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe'
    )) {
        try { $v = (Get-ItemProperty $c -ErrorAction Stop).'(default)'; if ($v -and (Test-Path $v)) { $browser = $v; break } } catch { }
    }
    if ($browser) {
        $url = if ($Action -eq 'dashboard') { "$base/" } else { "$base/chatroom" }
        $profileDir = Join-Path $stateDir ('browser\' + $Action)
        Start-Process -FilePath $browser -ArgumentList @(
            "--user-data-dir=$profileDir", '--profile-directory=Default', "--app=$url",
            '--no-first-run', '--no-default-browser-check'
        ) | Out-Null
    }
}
