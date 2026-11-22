# wrapper so the elevated run's output survives the elevated window closing
$ErrorActionPreference = 'Continue'
try {
    & (Join-Path $PSScriptRoot 'install-aitum.ps1') *> (Join-Path $PSScriptRoot 'aitum-install.log')
} catch {
    "FATAL: $($_.Exception.Message)" | Out-File -FilePath (Join-Path $PSScriptRoot 'aitum-install.log') -Append -Encoding utf8
    exit 1
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) { exit $LASTEXITCODE }
