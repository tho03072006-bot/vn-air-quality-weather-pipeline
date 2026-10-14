<#
.SYNOPSIS
    Create or rotate the two least-privilege serving logins, without a password
    reaching a file, a command line, or a shell history entry.

.DESCRIPTION
    This is the only supported way to run scripts\sql\create_serving_logins.sql. It
    exists for one reason: sqlcmd substitutes scripting variables textually, before
    SQL Server parses anything, so a password containing a single quote spliced into
    N'$(WEB_PASSWORD)' ends that literal early and turns the rest of the password
    into SQL. Quoting inside the script cannot repair damage done one layer above it.

    So the plaintext never enters the SQL text at all. This wrapper encodes each
    password as UTF-16LE hex -- characters 0-9 and a-f, none of which can close a
    string literal -- and the script decodes it back. The failure mode is removed
    rather than handled.

    Passwords come from the environment when they are already set, and otherwise
    from a prompt that does not echo. Neither path puts the value on a command line,
    where it would be visible to every other process on the machine through the
    process list and preserved in PSReadLine history.

.PARAMETER Server
    The instance holding the serving database. Defaults to tcp:localhost,1433, which
    is what .env addresses -- see the note in README about three instances on this
    machine all statically configured for port 1433.

.PARAMETER Persist
    Also store the passwords as user-scope environment variables, so the publish
    timer and Tomcat can read them on the next process start. Without it they are
    used for this run only, which is what a rotation rehearsal wants.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\create_serving_logins.ps1 -Persist
#>
[CmdletBinding()]
param(
    [string] $Server = 'tcp:localhost,1433',
    [switch] $Persist
)

$ErrorActionPreference = 'Stop'

function Read-Password {
    param([string] $Variable, [string] $Role)

    $existing = [Environment]::GetEnvironmentVariable($Variable, 'User')
    if ([string]::IsNullOrEmpty($existing)) {
        $existing = [Environment]::GetEnvironmentVariable($Variable, 'Process')
    }
    if (-not [string]::IsNullOrEmpty($existing)) {
        Write-Host "$Variable : taken from the environment."
        return $existing
    }

    Write-Host "$Variable is not set. Enter the password for $Role (input is hidden)."
    $secure = Read-Host -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        # The plaintext copy lives in unmanaged memory until this runs; a garbage
        # collector would not have freed it and a crash dump would have carried it.
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function ConvertTo-Utf16Hex {
    param([string] $Value)

    # UTF-16LE because that is what nvarchar holds. Encoding as UTF-8 here would round
    # trip every ASCII password correctly and quietly corrupt the first one containing
    # a character outside it -- the kind of defect that only shows up on somebody
    # else's keyboard.
    $bytes = [Text.Encoding]::Unicode.GetBytes($Value)
    -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

$webPassword = Read-Password -Variable 'VN_AIR_WEB_PASSWORD'     -Role 'vn_air_web (read-only)'
$pubPassword = Read-Password -Variable 'VN_AIR_PUBLISH_PASSWORD' -Role 'vn_air_publish (writer)'

if ($webPassword -eq $pubPassword) {
    throw 'The two logins must not share a password; separating them is the point.'
}

$sqlFile = Join-Path $PSScriptRoot 'sql\create_serving_logins.sql'
if (-not (Test-Path $sqlFile)) {
    throw "Cannot find $sqlFile"
}

# -v passes the hex on the command line, which is safe precisely because it is not the
# password: recovering it requires the encoding to be known, and more to the point the
# plaintext itself is never an argument to any process.
$webHex = ConvertTo-Utf16Hex $webPassword
$pubHex = ConvertTo-Utf16Hex $pubPassword

Write-Host "Applying $sqlFile against $Server as the current Windows user."
& sqlcmd -S $Server -E -C -b -i $sqlFile `
    -v WEB_PASSWORD_HEX=$webHex PUBLISH_PASSWORD_HEX=$pubHex

if ($LASTEXITCODE -ne 0) {
    throw "sqlcmd exited $LASTEXITCODE; no login was changed."
}

if ($Persist) {
    [Environment]::SetEnvironmentVariable('VN_AIR_WEB_PASSWORD', $webPassword, 'User')
    [Environment]::SetEnvironmentVariable('VN_AIR_PUBLISH_PASSWORD', $pubPassword, 'User')
    Write-Host 'Stored both passwords as user-scope environment variables.'
    Write-Host 'Restart Tomcat and re-register the publish timer so they pick up the new values.'
}

Write-Host ''
Write-Host 'Next: prove the boundary rather than assume it.'
Write-Host '    .\.venv\Scripts\python.exe scripts\verify_serving_logins.py'
