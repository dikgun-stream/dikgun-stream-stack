# Rebuild every scene browser source URL with a cache-busting query string.
#
# OBS's embedded Chromium caches overlay pages hard: editing a file under
# overlay-server/public/ and restarting OBS will still show the OLD page until
# the URL changes. This appends "&cb=<timestamp>" to each scene source so the
# cache key changes.
#
#   pwsh -File obs-build\refresh-scene-sources.ps1

[CmdletBinding()]
param(
    [string]$Server = 'http://127.0.0.1:7475'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\obs-ws.ps1"

# scene sources that render overlay pages, with their page path
$targets = [ordered]@{
    'OVL_StartingSoon'   = '/overlay/starting-soon'
    'OVL_BRB'            = '/overlay/brb'
    'OVL_Ending'         = '/overlay/ending'
    'OVL_StartingSoon_V' = '/overlay/starting-soon?v=1'
    'OVL_BRB_V'          = '/overlay/brb?v=1'
    'OVL_Ending_V'       = '/overlay/ending?v=1'
    'OVL_Deadlock_V'     = '/overlay/deadlock?left=26&bottom=640&width=150'
    'OVL_Alerts'         = '/overlay/alerts'
    'OVL_Alerts_V'       = '/overlay/alerts'
    'OVL_Chat'           = '/overlay/chat'
    'OVL_Chat_V'         = '/overlay/chat?bottom=470&width=520'
    'OVL_Goals'          = '/overlay/goals'
    'OVL_Goals_V'        = '/overlay/goals'
    'OVL_Topic'          = '/overlay/topic'
    'OVL_Topic_V'        = '/overlay/topic?bottom=520'
}

Connect-Obs | Out-Null
try {
    $cb = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    foreach ($name in $targets.Keys) {
        $path = $targets[$name]
        $sep = if ($path.Contains('?')) { '&' } else { '?' }
        $url = "$Server$path${sep}cb=$cb"
        try {
            $current = (Invoke-Obs 'GetInputSettings' @{ inputName = $name }).responseData.inputSettings.url
            Invoke-Obs 'SetInputSettings' @{
                inputName     = $name
                inputSettings = @{ url = $url }
            } | Out-Null
            Write-Host ("  {0,-20} refreshed" -f $name)
        } catch {
            Write-Host ("  {0,-20} SKIPPED ({1})" -f $name, ($_.Exception.Message -split "`n")[0])
        }
    }
} finally {
    Close-Obs | Out-Null
}

Write-Host ''
Write-Host 'Done. Scenes will re-fetch their pages on next display.'
