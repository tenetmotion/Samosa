<#
.SYNOPSIS
  Sign and verify a Samosa Windows installer with Microsoft Artifact Signing.

.DESCRIPTION
  Uses the same Azure Artifact Signing account and certificate profile as the
  CutMind release workflow. Credentials are supplied by the active Azure CLI
  identity; no signing secrets or certificates are stored in this repository.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$ArtifactSigningEndpoint = "https://eus.codesigning.azure.net",
    [string]$ArtifactSigningAccountName = "cutmind-signing",
    [string]$CertificateProfileName = "cutmindpublic",
    [string]$ArtifactSigningDlibPath = $env:SAMOSA_ARTIFACT_SIGNING_DLIB,
    [string]$TimestampUrl = "http://timestamp.acs.microsoft.com"
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $kitsRoot = Join-Path $programFilesX86 "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $kitsRoot) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }

    throw "signtool.exe was not found. Install the Windows SDK or add SignTool to PATH."
}

function Find-ArtifactSigningDlib {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        if (-not (Test-Path -LiteralPath $RequestedPath)) {
            throw "Artifact Signing client library was not found: $RequestedPath"
        }
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    $cacheRoot = Join-Path $PSScriptRoot "..\installer\downloads\artifact-signing"
    if (Test-Path -LiteralPath $cacheRoot) {
        $candidate = Get-ChildItem -LiteralPath $cacheRoot -Filter Azure.CodeSigning.Dlib.dll -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\bin\\x64\\Azure\.CodeSigning\.Dlib\.dll$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }

    throw (
        "Microsoft Artifact Signing client library was not found. Set " +
        "SAMOSA_ARTIFACT_SIGNING_DLIB or pass -ArtifactSigningDlibPath."
    )
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    throw "Installer was not found: $InstallerPath"
}

$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
if ([System.IO.Path]::GetExtension($InstallerPath) -ne ".exe") {
    throw "Samosa's Windows installer must be an .exe file: $InstallerPath"
}

$signTool = Find-SignTool
$dlibPath = Find-ArtifactSigningDlib -RequestedPath $ArtifactSigningDlibPath
$metadataPath = Join-Path ([System.IO.Path]::GetTempPath()) (
    "samosa-artifact-signing-{0}.json" -f [guid]::NewGuid().ToString("N")
)
$metadata = @{
    Endpoint = $ArtifactSigningEndpoint.TrimEnd("/")
    CodeSigningAccountName = $ArtifactSigningAccountName
    CertificateProfileName = $CertificateProfileName
    CorrelationId = "samosa-{0}" -f [guid]::NewGuid().ToString("N")
} | ConvertTo-Json
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($metadataPath, $metadata, $utf8WithoutBom)

try {
    Write-Host "> Signing $InstallerPath" -ForegroundColor Cyan
    $signArguments = @(
        "sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl,
        "/d", "Samosa", "/du", "https://github.com/tenetmotion/Samosa",
        "/dlib", $dlibPath, "/dmdf", $metadataPath, $InstallerPath
    )
    & $signTool @signArguments
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool signing failed with exit code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path -LiteralPath $metadataPath) {
        Remove-Item -LiteralPath $metadataPath -Force
    }
}

$signature = Get-AuthenticodeSignature -FilePath $InstallerPath
if ($signature.Status -ne "Valid") {
    throw "Authenticode verification failed: $($signature.Status)"
}

& $signTool verify /pa /all /v $InstallerPath
if ($LASTEXITCODE -ne 0) {
    throw "SignTool verification failed with exit code $LASTEXITCODE"
}

Write-Host "Signed and verified: $InstallerPath" -ForegroundColor Green
