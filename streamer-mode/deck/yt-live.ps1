# Bring OBS to the foreground so the Aitum Multistream dock is visible.
#
# WHY THIS ISN'T A REAL STREAM BUTTON:
# Aitum Multistream registers NO start/stop hotkey -- verified against all 354
# hotkeys OBS exposes. Aitum *Vertical* does (VerticalCanvasDockStartStreaming),
# which is why Twitch + YouTube-vertical are automatable and this one isn't.
# So this key makes the one manual click obvious rather than pretending.

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class F {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
"@

$p = Get-Process obs64 -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } |
     Select-Object -First 1

if ($p) {
    if ([F]::IsIconic($p.MainWindowHandle)) { [void][F]::ShowWindow($p.MainWindowHandle, 9) }
    [void][F]::SetForegroundWindow($p.MainWindowHandle)
}
