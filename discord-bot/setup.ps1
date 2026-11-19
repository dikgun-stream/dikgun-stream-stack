# DikGun Discord bot - interactive setup.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Walks through the two values that cannot be guessed: the bot token and the
# server id. Writes them into .env and then verifies everything connects.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envFile = Join-Path $here '.env'

function Read-Secret($prompt) {
    $secure = Read-Host -AsSecureString $prompt
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Get-EnvValue($text, $key) {
    $m = [regex]::Match($text, "(?m)^\s*$key\s*=(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ''
}

function Set-EnvValue($text, $key, $value) {
    if ($text -match "(?m)^\s*$key\s*=") {
        return [regex]::Replace($text, "(?m)^\s*$key\s*=.*$", "$key=$value")
    }
    return $text.TrimEnd() + "`r`n$key=$value`r`n"
}

Write-Host ""
Write-Host "  DikGun Discord bot setup" -ForegroundColor Cyan
Write-Host "  ------------------------"
Write-Host ""

if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $here '.env.example') $envFile
    Write-Host "  created .env from the example"
}

$text = Get-Content $envFile -Raw
$token = Get-EnvValue $text 'DISCORD_TOKEN'
$guild = Get-EnvValue $text 'DISCORD_GUILD_ID'

# ---------------------------------------------------------------- token
if ($token) {
    Write-Host "  A token is already saved (ends ...$($token.Substring([Math]::Max(0,$token.Length-4))))."
    $replace = Read-Host "  Replace it? (y/N)"
    if ($replace -eq 'y') { $token = '' }
}

if (-not $token) {
    Write-Host ""
    Write-Host "  STEP 1 - Bot token" -ForegroundColor Yellow
    Write-Host "    Open https://discord.com/developers/applications"
    Write-Host "    Pick your app -> Bot (left sidebar) -> Reset Token -> Copy"
    Write-Host ""
    Write-Host "    The token is shown once. If you lose it, reset it again."
    Write-Host "    It is pasted invisibly here - nothing will appear as you type."
    Write-Host ""
    $token = Read-Secret "  Paste token"
    if (-not $token) { Write-Host "  no token entered - stopping" -ForegroundColor Red; exit 1 }
    if ($token -match '\s') { Write-Host "  token contains whitespace; it was probably cut short. Re-run." -ForegroundColor Red; exit 1 }
}

# ---------------------------------------------------------------- guild id
if (-not $guild) {
    Write-Host ""
    Write-Host "  STEP 2 - Server id" -ForegroundColor Yellow
    Write-Host "    In Discord: User Settings (gear) -> Advanced -> Developer Mode ON"
    Write-Host "    Right-click your server icon -> Copy Server ID"
    Write-Host ""
    $guild = Read-Host "  Paste server id"
    if ($guild -notmatch '^\d{15,25}$') {
        Write-Host "  that does not look like a server id (should be digits only)" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------- write
$text = Set-EnvValue $text 'DISCORD_TOKEN' $token
$text = Set-EnvValue $text 'DISCORD_GUILD_ID' $guild
Set-Content -Path $envFile -Value $text -Encoding UTF8
Write-Host ""
Write-Host "  saved to $envFile" -ForegroundColor Green

# ---------------------------------------------------------------- verify
Write-Host ""
Write-Host "  Checking..." -ForegroundColor Cyan
python bot.py --check
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "  Everything checks out." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next:"
    Write-Host "    1. Invite the bot if you have not already (link is in SETUP.md)"
    Write-Host "    2. python bot.py --provision     (builds channels and roles)"
    Write-Host "    3. python bot.py                 (runs the bot)"
} else {
    Write-Host "  Something is still missing - see the messages above." -ForegroundColor Yellow
}

Write-Host ""
