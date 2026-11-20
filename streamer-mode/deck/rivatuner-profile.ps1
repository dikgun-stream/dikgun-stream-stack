# Apply a RivaTuner launcher profile by triggering its own hotkey.
#
#   powershell -File rivatuner-profile.ps1 -List
#   powershell -File rivatuner-profile.ps1 -Name Night
#   powershell -File rivatuner-profile.ps1 -Name Night -Restart
#
# Why trigger the hotkey instead of setting things directly
#   The profiles live inside RivaTuner, and RivaTuner already knows how to apply
#   them. Its launcher entries are bound to global hotkeys, which it registers
#   with RegisterHotKey - so simulated key input reaches it exactly like a real
#   keypress. That avoids reimplementing gamma/colour-scheme application and
#   keeps working if the profiles are edited.
#
#   The bindings are read from the registry every run, so rebinding a profile in
#   the RivaTuner UI needs no change here.
#
# -Restart
#   A GPU driver update leaves RivaTuner's kernel driver hooked to a display
#   driver instance that no longer exists. The app looks alive but ignores its
#   hotkeys. -Restart stops and reloads that driver first, which is the fix for
#   "it stopped working after I updated drivers" without rebooting the machine.

[CmdletBinding()]
param(
    [string]$Name,
    [switch]$List,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'

$LauncherKey = 'HKLM:\SOFTWARE\WOW6432Node\Unwinder\RivaTuner\Launcher'
$ExeDir = 'C:\Program Files (x86)\RivaTuner v2.24 MSI Master Overclocking Arena 2009 edition'
$Exe = Join-Path $ExeDir 'RivaTuner.exe'
$Helper = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'restart-rivatuner.ps1'

# ------------------------------------------------------------------- decoding
# RivaTuner stores a hotkey as (modifiers << 16) | virtual-key.
$MOD = @{ ALT = 0x1; CTRL = 0x2; SHIFT = 0x4; WIN = 0x8 }

$VK_NAMES = @{
    0x21 = 'PageUp'; 0x22 = 'PageDown'; 0x23 = 'End'; 0x24 = 'Home'
    0x25 = 'Left'; 0x26 = 'Up'; 0x27 = 'Right'; 0x28 = 'Down'
    0x2D = 'Insert'; 0x2E = 'Delete'; 0x13 = 'Pause'; 0x2C = 'PrintScreen'
    0x70 = 'F1'; 0x71 = 'F2'; 0x72 = 'F3'; 0x73 = 'F4'; 0x74 = 'F5'; 0x75 = 'F6'
    0x76 = 'F7'; 0x77 = 'F8'; 0x78 = 'F9'; 0x79 = 'F10'; 0x7A = 'F11'; 0x7B = 'F12'
    0x20 = 'Space'; 0x0D = 'Enter'; 0x1B = 'Escape'; 0x09 = 'Tab'; 0x08 = 'Backspace'
}

function Get-KeyName([int]$vk) {
    if ($VK_NAMES.ContainsKey($vk)) { return $VK_NAMES[$vk] }
    if ($vk -ge 0x41 -and $vk -le 0x5A) { return [string][char]$vk }
    if ($vk -ge 0x30 -and $vk -le 0x39) { return [string][char]$vk }
    return ('VK_{0:X2}' -f $vk)
}

function ConvertFrom-RivaHotkey([int]$raw) {
    $vk = $raw -band 0xFFFF
    # RivaTuner's high word is not plain Win32 MOD_* flags. Verified against its
    # UI: 'Night' reads 655393 = 0x000A0021 and the launcher editor shows
    # 'Ctrl + Page Up'. So 0x0A carries Ctrl (0x02) plus 0x08, which is a flag
    # (the entry is a system-wide hotkey), not MOD_WIN. Masking to 0x07 gives the
    # real modifiers and reproduces the UI exactly.
    $mods = ($raw -shr 16) -band 0x07
    $parts = @()
    if ($mods -band $MOD.CTRL)  { $parts += 'Ctrl' }
    if ($mods -band $MOD.ALT)   { $parts += 'Alt' }
    if ($mods -band $MOD.SHIFT) { $parts += 'Shift' }
    if ($mods -band $MOD.WIN)   { $parts += 'Win' }
    $parts += (Get-KeyName $vk)
    return [pscustomobject]@{
        Raw        = $raw
        VirtualKey = $vk
        Modifiers  = $mods
        Text       = ($parts -join '+')
    }
}

# -------------------------------------------------------------------- profiles
function Get-Profiles {
    if (-not (Test-Path $LauncherKey)) {
        Write-Host "  no RivaTuner launcher key at $LauncherKey" -ForegroundColor Red
        return @()
    }
    $out = @()
    foreach ($k in (Get-ChildItem $LauncherKey -ErrorAction SilentlyContinue)) {
        $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
        if (-not $p.Name) { continue }
        $hk = 0
        if ($null -ne $p.HotKey) { $hk = [int]$p.HotKey }
        $out += [pscustomobject]@{
            Name       = [string]$p.Name
            Scheme     = [string]$p.ColorScheme
            Index      = [int]$p.Index
            Hotkey     = $(if ($hk -gt 0) { ConvertFrom-RivaHotkey $hk } else { $null })
        }
    }
    return ($out | Sort-Object Index)
}

# ---------------------------------------------------------------------- input
if (-not ('RivaKey' -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class RivaKey
{
    [DllImport("user32.dll", SetLastError = true)]
    static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    const uint KEYUP = 0x0002;
    const byte VK_CONTROL = 0x11, VK_LWIN = 0x5B, VK_SHIFT = 0x10, VK_MENU = 0x12;

    // Press the modifiers, tap the key, release in reverse. Real input events,
    // which is what a RegisterHotKey registration reacts to.
    public static void Send(int modifiers, int vk)
    {
        var held = new System.Collections.Generic.List<byte>();
        if ((modifiers & 0x2) != 0) { held.Add(VK_CONTROL); }
        if ((modifiers & 0x8) != 0) { held.Add(VK_LWIN); }
        if ((modifiers & 0x4) != 0) { held.Add(VK_SHIFT); }
        if ((modifiers & 0x1) != 0) { held.Add(VK_MENU); }

        foreach (var m in held) keybd_event(m, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(40);
        keybd_event((byte)vk, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(40);
        keybd_event((byte)vk, 0, KEYUP, UIntPtr.Zero);
        System.Threading.Thread.Sleep(40);
        for (int i = held.Count - 1; i >= 0; i--) keybd_event(held[i], 0, KEYUP, UIntPtr.Zero);
    }
}
'@
}

$profiles = Get-Profiles

if ($List -or (-not $Name)) {
    Write-Host ''
    Write-Host '  RivaTuner launcher profiles' -ForegroundColor Cyan
    Write-Host '  --------------------------'
    if (-not $profiles.Count) {
        Write-Host '  none found'
    } else {
        foreach ($p in $profiles) {
            $hk = if ($p.Hotkey) { $p.Hotkey.Text } else { '(no hotkey)' }
            Write-Host ("  {0,-10} scheme={1,-8} hotkey={2}" -f $p.Name, $p.Scheme, $hk)
        }
    }
    Write-Host ''
    Write-Host '  Apply one with:  -Name Night' -ForegroundColor DarkGray
    Write-Host ''
    if (-not $Name) { return }
}

# ---------------------------------------------------------------------- match
$target = $profiles | Where-Object { $_.Name -eq $Name }
if (-not $target) {
    $target = $profiles | Where-Object { $_.Name -like "*$Name*" }
}
if (-not $target) {
    Write-Host "  no profile matching '$Name'" -ForegroundColor Red
    Write-Host "  available: $(($profiles | ForEach-Object { $_.Name }) -join ', ')"
    return
}
if ($target -is [array]) { $target = $target[0] }

if (-not $target.Hotkey) {
    Write-Host "  '$($target.Name)' has no hotkey bound in RivaTuner, so it cannot be triggered." -ForegroundColor Red
    Write-Host '  Bind one in RivaTuner: Launcher -> the entry -> Hotkey, then re-run.' -ForegroundColor DarkGray
    return
}

# -------------------------------------------------------------------- restart
if ($Restart) {
    if (-not (Test-Path $Helper)) {
        Write-Host "  restart helper not found: $Helper" -ForegroundColor Red
    } else {
        Write-Host '  restarting RivaTuner driver first...' -ForegroundColor Yellow
        # never pipe this to Out-Null: the helper needs elevation, and swallowing
        # its output is what made a failed restart look like a successful one.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $Helper -Fix
        Start-Sleep -Seconds 2
    }
}

$proc = Get-Process RivaTuner -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host '  RivaTuner is not running - launching it' -ForegroundColor Yellow
    Start-Process -FilePath $Exe -WorkingDirectory $ExeDir | Out-Null
    Start-Sleep -Seconds 6
}

Write-Host ("  applying '{0}' via {1}" -f $target.Name, $target.Hotkey.Text)
[RivaKey]::Send($target.Hotkey.Modifiers, $target.Hotkey.VirtualKey)
Start-Sleep -Milliseconds 400
Write-Host "  sent. If nothing changed, RivaTuner has lost its driver hook - re-run with -Restart." -ForegroundColor Green
