# Restart RivaTuner's kernel driver without rebooting.
#
#   powershell -File restart-rivatuner.ps1 -Check            report state only
#   powershell -File restart-rivatuner.ps1 -Fix              restart the app
#   powershell -File restart-rivatuner.ps1 -Fix -Driver      also reload the driver
#
# -Fix alone is the normal repair. Measured on this machine: the 4-day-old app
# had a stale hook while RivaTuner64 stayed loaded the whole time, and a plain
# app restart restored it. RivaTuner re-attaches to the current display driver
# when it starts, so the kernel driver does not need to be unloaded at all.
#
# -Driver is the belt-and-braces path for when an app restart is not enough. It
# needs administrator rights and a UAC prompt, because stopping a kernel driver
# does.
#
# Why this is needed
#   RivaTuner 2.24 installs a kernel driver (RivaTuner64) that hooks the display
#   driver. A GPU driver update replaces the display driver underneath it, so the
#   hook points at an instance that no longer exists. The app keeps running and
#   ignores its hotkeys. The usual fix is a reboot, because a kernel driver
#   cannot normally be unloaded.
#
#   That driver reports STOPPABLE, so it can be cycled live - but stopping a
#   kernel driver requires administrator rights, and sc.exe fails with
#   "Access is denied (5)" without them. An earlier version of this script ran
#   unelevated, failed at exactly that step, and swallowed the error, which made
#   it look like a no-op. It now escalates itself when it needs to.
#
# What it cannot tell you
#   RivaTuner.exe is 32-bit. A 64-bit PowerShell cannot enumerate a 32-bit
#   process's modules, so the hook DLL cannot be inspected from here. The script
#   used to report "NOT attached" for that reason, which was a false negative -
#   the only trustworthy signal is whether the hotkey changes anything on screen.

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Fix,
    [switch]$Driver,           # also cycle the kernel driver (needs admin)
    [switch]$Elevated          # internal: set when this instance was relaunched
)

$ErrorActionPreference = 'Stop'

$Svc    = 'RivaTuner64'
$ExeDir = 'C:\Program Files (x86)\RivaTuner v2.24 MSI Master Overclocking Arena 2009 edition'
$Exe    = Join-Path $ExeDir 'RivaTuner.exe'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-SvcState {
    $s = Get-CimInstance Win32_SystemDriver -Filter "Name='$Svc'" -ErrorAction SilentlyContinue
    if (-not $s) { return 'absent' }
    return $s.State
}

function Show-State {
    $svcState = Get-SvcState
    $proc = Get-Process RivaTuner -ErrorAction SilentlyContinue
    $gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1

    Write-Host ''
    Write-Host '  RivaTuner state' -ForegroundColor Cyan
    Write-Host '  ---------------'
    $col = if ($svcState -eq 'Running') { 'Green' } else { 'DarkGray' }
    Write-Host "  driver $Svc : $svcState" -ForegroundColor $col

    if ($proc) {
        $up = ''
        try { $up = $proc.StartTime.ToString('yyyy-MM-dd HH:mm:ss') } catch { }
        Write-Host "  app            : running (pid $($proc.Id), since $up)" -ForegroundColor Green
    } else {
        Write-Host '  app            : not running' -ForegroundColor DarkGray
    }

    if ($gpu) { Write-Host "  display driver : $($gpu.Name) $($gpu.DriverVersion)" }
    Write-Host "  elevated       : $(Test-Elevated)"
    Write-Host ''
}

if ($Check -or (-not $Fix)) {
    Show-State
    if (-not $Fix) {
        Write-Host '  -Fix stops the driver and restarts the app. It will ask for' -ForegroundColor DarkGray
        Write-Host '  administrator rights, because stopping a kernel driver needs them.' -ForegroundColor DarkGray
        Write-Host ''
    }
    return
}

# ------------------------------------------------------------------ escalate
# Always escalate for -Fix. RivaTuner's manifest uses a placeholder execution
# level, so whether its process is elevated depends on how it was launched - the
# original boot-time instance was killable unelevated, the one started from here
# was not ("Access is denied"). Escalating unconditionally is one prompt either
# way and cannot fail for that reason.
if (-not (Test-Elevated)) {
    Write-Host ''
    Write-Host '  Stopping a kernel driver needs administrator rights.' -ForegroundColor Yellow
    Write-Host '  Re-launching elevated - accept the prompt to continue.' -ForegroundColor Yellow
    Write-Host ''
    $scriptPath = $MyInvocation.MyCommand.Definition
    try {
        $p = Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', $scriptPath, '-Fix', '-Elevated') `
            -Verb RunAs -Wait -PassThru
        if ($p.ExitCode -ne 0) {
            Write-Host "  elevated run exited with code $($p.ExitCode)" -ForegroundColor Red
        }
    } catch {
        Write-Host '  elevation was declined or failed.' -ForegroundColor Red
        Write-Host '  Nothing was changed. RivaTuner still needs a reboot in that case.' -ForegroundColor DarkGray
    }
    Write-Host ''
    Show-State
    return
}

# ---------------------------------------------------------------------- fix
Show-State
Write-Host '  Restarting...' -ForegroundColor Yellow

$proc = Get-Process RivaTuner -ErrorAction SilentlyContinue
if ($proc) {
    try { [void]$proc.CloseMainWindow() } catch { }
    if (-not $proc.WaitForExit(4000)) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch {
            Write-Host "  could not close RivaTuner: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host '  Nothing changed.' -ForegroundColor Red
            return
        }
    }
    Start-Sleep -Milliseconds 800
    if (Get-Process RivaTuner -ErrorAction SilentlyContinue) {
        Write-Host '  RivaTuner is still running - aborting before touching the driver.' -ForegroundColor Red
        return
    }
    Write-Host '  app closed' -ForegroundColor DarkGray
}

if ($Driver -and (Get-SvcState) -eq 'Running') {
    $out = & sc.exe stop $Svc 2>&1
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-SvcState) -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
    }
    if ((Get-SvcState) -eq 'Running') {
        Write-Host ''
        Write-Host '  The driver refused to stop:' -ForegroundColor Red
        $out | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Write-Host '  Relaunching the app so you are no worse off than before.' -ForegroundColor DarkGray
        Start-Process -FilePath $Exe -WorkingDirectory $ExeDir | Out-Null
        return
    }
    Write-Host '  driver stopped' -ForegroundColor DarkGray
}

Start-Process -FilePath $Exe -WorkingDirectory $ExeDir | Out-Null
$deadline = (Get-Date).AddSeconds(20)
while (-not (Get-Process RivaTuner -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
}
Start-Sleep -Seconds 2

Show-State
if ($Driver) {
    Write-Host '  App restarted and driver reloaded.' -ForegroundColor Green
} else {
    Write-Host '  App restarted. RivaTuner re-attaches to the current display driver on' -ForegroundColor Green
    Write-Host '  start, which is normally the whole repair.' -ForegroundColor Green
}
Write-Host '  Now try the profile hotkey. If the screen still does not change, retry' -ForegroundColor DarkGray
Write-Host '  with -Driver, or check the profile association in RivaTuner itself.' -ForegroundColor DarkGray
Write-Host ''
