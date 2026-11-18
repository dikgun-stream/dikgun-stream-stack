# wrapper so the elevated run's output survives the elevated window closing
$ErrorActionPreference = 'Continue'
try {
    & 'C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\install-aitum.ps1' *> 'C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\aitum-install.log'
} catch {
    "FATAL: $($_.Exception.Message)" | Out-File -FilePath 'C:\Users\JR\Documents\deepseek-harness\default-workspace\obs-build\aitum-install.log' -Append -Encoding utf8
    exit 1
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) { exit $LASTEXITCODE }
