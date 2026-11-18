# Deck button helper. One entry point so each .bat is a one-liner.
param(
    [Parameter(Mandatory)][ValidateSet('golive', 'stop', 'panic', 'mic', 'hint', 'dashboard', 'chat')]
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
        'golive' { Post '/api/live' '{"action":"start"}' }
        'stop'   { Post '/api/live' '{"action":"stop"}' }
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
