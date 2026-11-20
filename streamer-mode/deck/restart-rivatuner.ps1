# Restart RivaTuner's kernel driver without rebooting.
#
#   powershell -File restart-rivatuner.ps1 -Check    report state only, changes nothing
#   powershell -File restart-rivatuner.ps1 -Fix      stop driver, restart app
#
# Why this is needed
#   RivaTuner 2.24 installs a kernel driver (RivaTuner64) that hooks the display
#   driver to do fan control and overclocking. A GPU driver update replaces the
#   display driver underneath it. The hook now points at a driver instance that
#   no longer exists, so RivaTuner appears to run but does nothing - and the
#   usual fix is a reboot, because a kernel driver cannot normally be unloaded.
#
#   This driver reports STOPPABLE, so it can be stopped and reloaded live. The
#   sequence matters: the app has to release the driver before it can stop, and
#   the driver has to be fully stopped before the app is allowed to reload it.

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Fix
)

$ErrorActionPreference = 'Stop'

$Svc      = 'RivaTuner64'
$ExeDir   = 'C:\Program Files (x86)\RivaTuner v2.24 MSI Master Overclocking Arena 2009 edition'
$Exe      = Join-Path $ExeDir 'RivaTuner.exe'

function Get-SvcState {
    $s = Get-CimInstance Win32_SystemDriver -Filter "Name='$Svc'" -ErrorAction SilentlyContinue
    if (-not $s) { return 'absent' }
    return $s.State
}

function Show-State {
    $svcState = Get-SvcState
    $proc = Get-Process RivaTuner -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host '  RivaTuner state' -ForegroundColor Cyan
    Write-Host '  ---------------'

    $svcColour = if ($svcState -eq 'Running') { 'Green' } else { 'DarkGray' }
    Write-Host "  driver $Svc : $svcState" -ForegroundColor $svcColour

    if ($proc) {
        $up = ''
        try { $up = $proc.StartTime.ToString('yyyy-MM-dd HH:mm') } catch { }
        Write-Host "  app            : running (pid $($proc.Id), since $up)" -ForegroundColor Green
    } else {
        Write-Host '  app            : not running' -ForegroundColor DarkGray
    }

    # The hook DLL is the thing that actually does the work. If the app is up but
    # the hook is not attached, RivaTuner is in the broken state users describe as
    # "stopped working".
    if ($proc) {
        $hooked = $null
        try {
            $hooked = [bool]($proc.Modules | Where-Object { $_.ModuleName -eq 'RivaTunerHooks.dll' })
        } catch {
            # 64-bit PowerShell cannot enumerate a 32-bit process's modules
            $hooked = $null
        }
        if ($hooked -eq $true) {
            Write-Host '  hook dll       : attached' -ForegroundColor Green
        } elseif ($hooked -eq $false) {
            Write-Host '  hook dll       : NOT attached  <- this is the broken state' -ForegroundColor Red
        } else {
            Write-Host '  hook dll       : unknown (cannot inspect a 32-bit process from here)'
        }
    }

    $gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1
    if ($gpu) {
        Write-Host "  display driver : $($gpu.Name) $($gpu.DriverVersion)"
    }
    Write-Host ''
}

if ($Check -or (-not $Fix)) {
    Show-State
    if (-not $Fix) {
        Write-Host '  Run with -Fix to restart the driver pod.' -ForegroundColor DarkGray
        Write-Host ''
    }
    return
}

# ---------------------------------------------------------------------- fix
Show-State
Write-Host '  Restarting...' -ForegroundColor Yellow

# 1. close the app so it releases the driver
$proc = Get-Process RivaTuner -ErrorAction SilentlyContinue
if ($proc) {
    try { [void]$proc.CloseMainWindow() } catch { }
    if (-not $proc.WaitForExit(4000)) {
        try { Stop-Process -Id $proc.Id -Force } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process RivaTuner -ErrorAction SilentlyContinue) {
        Write-Host '  could not close RivaTuner - aborting, nothing has been changed' -ForegroundColor Red
        return
    }
    Write-Host '  app closed' -ForegroundColor DarkGray
}

# 2. stop the kernel driver
if ((Get-SvcState) -eq 'Running') {
    & sc.exe stop $Svc | Out-Null
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-SvcState) -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
    }
    if ((Get-SvcState) -eq 'Running') {
        Write-Host ''
        Write-Host '  The driver refused to stop. Nothing further was attempted.' -ForegroundColor Red
        Write-Host '  Relaunching RivaTuner so you are no worse off than before.' -ForegroundColor DarkGray
        Start-Process -FilePath $Exe -WorkingDirectory $ExeDir | Out-Null
        return
    }
    Write-Host '  driver stopped' -ForegroundColor DarkGray
}

# 3. relaunch - it loads the driver on demand and re-hooks the live display driver
Start-Process -FilePath $Exe -WorkingDirectory $ExeDir | Out-Null
$deadline = (Get-Date).AddSeconds(20)
while (-not (Get-Process RivaTuner -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
}

Start-Sleep -Seconds 2
Show-State
Write-Host '  Done. Fan and clock settings are reapplied by RivaTuner itself; if you' -ForegroundColor Green
Write-Host '  use a saved profile, re-apply it from the tray icon.' -ForegroundColor Green
Write-Host ''
