# Starts, stops and reports on the DikGun Discord bot.
#
#   powershell -File bot-control.ps1 start
#   powershell -File bot-control.ps1 stop
#   powershell -File bot-control.ps1 restart
#   powershell -File bot-control.ps1 status
#
# Designed to be the thing a Stream Deck button runs: it never leaves a console
# window up, it refuses to start a second copy, and it writes a log you can read
# when something goes wrong silently.

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'toggle')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pidFile = Join-Path $here '.bot.pid'
$logDir = Join-Path $here 'logs'
$stamp = Get-Date -Format 'yyyyMMdd'
$logFile = Join-Path $logDir ("bot-{0}.log" -f $stamp)        # the bot's own stdout
$controlLog = Join-Path $logDir ("control-{0}.log" -f $stamp) # what this script did

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# ---------------------------------------------------------------- helpers
function Get-BotProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $raw = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $pidValue = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$pidValue)) { return $null }
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    # guard against the pid having been reused by something else
    if ($proc.ProcessName -notmatch 'python') { return $null }
    return $proc
}

function Write-Log($message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    try {
        Add-Content -Path $controlLog -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch {
        # never let logging break the action
    }
}

function Get-PythonPath {
    $candidates = @(
        "C:\Users\JR\AppData\Local\Programs\Python\Python312\python.exe",
        "python"
    )
    foreach ($c in $candidates) {
        if ($c -eq 'python') { return 'python' }
        if (Test-Path $c) { return $c }
    }
    return 'python'
}

# ---------------------------------------------------------------- actions
function Start-Bot {
    $existing = Get-BotProcess
    if ($existing) {
        Write-Host "  already running (pid $($existing.Id))" -ForegroundColor Yellow
        Write-Log "start skipped, already running pid $($existing.Id)"
        return 0
    }

    $envFile = Join-Path $here '.env'
    if (-not (Test-Path $envFile)) {
        Write-Host "  no .env - run setup.ps1 first" -ForegroundColor Red
        Write-Log "start failed: .env missing"
        return 1
    }

    $python = Get-PythonPath
    Write-Log "starting bot with $python"

    # Redirect both streams to the log. -WindowStyle Hidden keeps the console
    # from flashing when triggered from a Stream Deck button.
    $args = @{
        FilePath               = $python
        ArgumentList           = '-u bot.py'
        WorkingDirectory       = $here
        WindowStyle            = 'Hidden'
        RedirectStandardOutput = $logFile
        RedirectStandardError  = (Join-Path $logDir 'bot-error.log')
        PassThru               = $true
    }
    $proc = Start-Process @args
    $proc.Id | Set-Content -Path $pidFile -Encoding UTF8

    Start-Sleep -Seconds 4
    $check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($check) {
        Write-Host "  started (pid $($proc.Id))" -ForegroundColor Green
        Write-Host "  log: $logFile"
        Write-Log "started pid $($proc.Id)"
        return 0
    }

    Write-Host "  failed to stay running - check the log" -ForegroundColor Red
    Write-Host "  $logFile"
    $errFile = Join-Path $logDir 'bot-error.log'
    if (Test-Path $errFile) {
        Get-Content $errFile -Tail 12 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow }
    }
    Write-Log "start failed: process exited"
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return 1
}

function Stop-Bot {
    $proc = Get-BotProcess
    if (-not $proc) {
        Write-Host "  not running" -ForegroundColor Yellow
        Write-Log "stop skipped, not running"
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        return 0
    }
    Write-Host "  stopping pid $($proc.Id)..."
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        Write-Host "  could not stop pid $($proc.Id)" -ForegroundColor Red
        Write-Log "stop failed pid $($proc.Id)"
        return 1
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "  stopped" -ForegroundColor Green
    Write-Log "stopped pid $($proc.Id)"
    return 0
}

function Show-Status {
    $proc = Get-BotProcess
    if ($proc) {
        $started = try { $proc.StartTime.ToString('HH:mm:ss') } catch { 'unknown' }
        Write-Host "  RUNNING  pid $($proc.Id)  since $started" -ForegroundColor Green
    } else {
        Write-Host "  STOPPED" -ForegroundColor DarkGray
    }

    # is the overlay server up? the bot is useless without it
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:7475/api/live/status' -TimeoutSec 3
        Write-Host "  overlay server: up"
    } catch {
        Write-Host "  overlay server: DOWN (start it or the bot has nothing to talk to)" -ForegroundColor Yellow
    }
    return 0
}

# ---------------------------------------------------------------- dispatch
switch ($Action) {
    'start'   { exit (Start-Bot) }
    'stop'    { exit (Stop-Bot) }
    'restart' { $null = Stop-Bot; exit (Start-Bot) }
    'toggle'  { if (Get-BotProcess) { exit (Stop-Bot) } else { exit (Start-Bot) } }
    default   { exit (Show-Status) }
}
