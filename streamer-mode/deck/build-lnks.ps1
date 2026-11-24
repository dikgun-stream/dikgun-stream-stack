# Build .lnk launchers for every deck action, replacing the .bat wrappers.
#
# Why: the Stream Dock app runs its keys through a ShellExecute-style "open"
# action. Pointing that at a .bat makes Windows show "Open File - Security
# Warning" for every press, and a dismissed prompt = the key silently doing
# nothing. A local .lnk that launches PowerShell directly carries no script
# trust prompt and cannot be silently eaten.
#
# Regenerate after changing any action:
#   powershell -NoProfile -ExecutionPolicy Bypass -File build-lnks.ps1
#   then rebuild the deck profile (build_streamdeck_profile.py) and restart
#   the Stream Dock app.

$ErrorActionPreference = 'Stop'
$deck = Split-Path -Parent $MyInvocation.MyCommand.Path
$pwsh = 'C:\Program Files\PowerShell\7\pwsh.exe'
if (-not (Test-Path $pwsh)) { throw "pwsh not found at $pwsh" }

$sh = New-Object -ComObject WScript.Shell

# Deck-Action.ps1 actions  (bat basename -> -Action value)
$deckActions = @{
    'assist'         = 'assist'
    'chat'           = 'chat'
    'damage'         = 'damage'
    'dashboard'      = 'dashboard'
    'go-live'        = 'golive'
    'hint'           = 'hint'
    'kill'           = 'kill'
    'mic-toggle'     = 'mic'
    'panic'          = 'panic'
    'prep'           = 'prep'
    'stop-live'      = 'stop'
    'stream-all'     = 'streamall'
    'tiktok'         = 'tiktok'
    'tiktok-go'      = 'tiktokgo'
    'tiktok-state'   = 'tiktokstate'
    'tiktok-stop'    = 'tiktokstop'
}

# Streamer-Mode.ps1 modes
$modeActions = @{
    'stream-mode'    = '-Mode stream'
    'dev-mode'       = '-Mode dev'
    'restore'        = '-Restore'
}

# plain script launchers (bat basename -> ps1 name)
$scriptActions = @{
    'launch-deadlock' = 'Launch-Deadlock.ps1'
    'launch-eft'      = 'Launch-EFT.ps1'
    'yt-live'         = 'yt-live.ps1'
}

function New-Launcher([string]$name, [string]$argStr) {
    $out = Join-Path $deck "$name.lnk"
    $lnk = $sh.CreateShortcut($out)
    $lnk.TargetPath = $pwsh
    $lnk.Arguments = $argStr
    $lnk.WorkingDirectory = $deck
    $lnk.WindowStyle = 7          # minimized
    $lnk.Description = "deck: $name"
    $lnk.Save()
    Write-Host ("  {0,-18} {1}" -f "$name.lnk", $argStr.Substring(0, [Math]::Min(96, $argStr.Length)))
}

foreach ($k in $deckActions.Keys) {
    New-Launcher $k "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$deck\Deck-Action.ps1`" -Action $($deckActions[$k])"
}
foreach ($k in $modeActions.Keys) {
    New-Launcher $k "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Split-Path $deck -Parent)\Streamer-Mode.ps1`" $($modeActions[$k])"
}
foreach ($k in $scriptActions.Keys) {
    New-Launcher $k "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$deck\$($scriptActions[$k])`""
}

Write-Host "  done: $($deckActions.Count + $modeActions.Count + $scriptActions.Count) launchers in $deck"
