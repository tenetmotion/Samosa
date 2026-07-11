param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Destination
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Destination) {
    $Destination = Join-Path $Root "dist"
}
$Destination = [System.IO.Path]::GetFullPath($Destination)
$Stage = Join-Path $Destination "Samosa-$Version"
$Zip = Join-Path $Destination "Samosa-$Version-source.zip"

$ForbiddenPatterns = @(
    'config.json$', '__pycache__', '\.pyc$', '\.log$', '\.aex$', '\.plugin$',
    '\.prm$', '\.zxp$', '\.msi$', '\.pt$', '\.pth$', '\.ckpt$',
    '\.safetensors$', '\.onnx$', '(^|[\\/])checkpoints?([\\/]|$)',
    '(^|[\\/])models?([\\/]|$)', '(^|[\\/])\.venv([\\/]|$)',
    '(^|[\\/])\.uv([\\/]|$)', '(^|[\\/])\.git([\\/]|$)',
    '(^|[\\/])dist([\\/]|$)'
)

$SourceFiles = Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
    $Relative = $_.FullName.Substring($Root.Length + 1)
    -not ($ForbiddenPatterns | Where-Object { $Relative -match $_ })
}

if (-not ($SourceFiles | Where-Object { $_.Name -eq 'LICENSE' })) {
    throw "LICENSE is missing"
}
if (Test-Path -LiteralPath (Join-Path $Root 'panel\config.json')) {
    throw "Generated panel/config.json must not be released"
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

foreach ($File in $SourceFiles) {
    $Relative = $File.FullName.Substring($Root.Length + 1)
    $Target = Join-Path $Stage $Relative
    New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
    Copy-Item -LiteralPath $File.FullName -Destination $Target
}

Compress-Archive -Path $Stage -DestinationPath $Zip -CompressionLevel Optimal
Write-Host "Source directory: $Stage"
Write-Host "Source archive:   $Zip"
