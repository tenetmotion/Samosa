param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.1.0",
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDir) { $OutputDir = Join-Path $Root "dist\installer" }
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$CompilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Compiler) { throw "Inno Setup 6 was not found. Install JRSoftware.InnoSetup with winget." }

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$Script = Join-Path $Root "installer\Samosa.iss"
& $Compiler "/DMyAppVersion=$Version" "/O$OutputDir" $Script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$Installer = Join-Path $OutputDir "Samosa-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $Installer)) { throw "Installer output was not created: $Installer" }
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Installer
Write-Host "Installer: $Installer"
Write-Host "SHA-256:  $($Hash.Hash)"
