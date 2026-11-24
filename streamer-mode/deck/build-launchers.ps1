# Build the per-action exe launchers from DeckLauncher.cs.
#
# Why exes: the Stream Dock "open" action resolves .lnk files to their target and
# drops the arguments (observed: bare interactive pwsh opened on STREAM MODE), and
# .bat files get the Windows script trust prompt. A local .exe has neither problem.
#
# Each exe is the SAME program copied under the action's name; it maps its own
# filename to the correct PowerShell command line and launches pwsh hidden.
# Rebuild after editing DeckLauncher.cs, then regenerate the deck profile and
# restart the Stream Dock app.

$ErrorActionPreference = 'Stop'
$deck = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $deck 'DeckLauncher.cs'
$exe = Join-Path $deck 'deck-launcher.exe'

# compile once (in-box C# compiler, no SDK needed)
Add-Type -TypeDefinition (Get-Content $src -Raw) `
         -OutputAssembly $exe `
         -OutputType WindowsApplication `
         -ReferencedAssemblies 'System.dll','System.Core.dll'

# one copy per action; the program behaves according to its own file name
$names = @(
    'assist','chat','damage','dashboard','go-live','hint','kill','mic-toggle',
    'panic','prep','stop-live','stream-all','tiktok','tiktok-go','tiktok-state','tiktok-stop',
    'stream-mode','dev-mode','restore',
    'launch-deadlock','launch-eft','yt-live'
)
foreach ($n in $names) {
    Copy-Item $exe (Join-Path $deck "$n.exe") -Force
    Write-Host "  $n.exe"
}
Write-Host "  done: $($names.Count) launchers (each a copy of deck-launcher.exe)"
