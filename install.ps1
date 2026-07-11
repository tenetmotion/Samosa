param(
    [string]$SammieRepo,
    [int]$Port = 43831,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$PluginId = "com.tenet.samosa.roto"
$Target = Join-Path $env:APPDATA "Adobe\CEP\extensions\$PluginId"

if ($Uninstall) {
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    Write-Host "Removed $Target"
    exit 0
}

if (-not $SammieRepo) {
    $Candidates = @(
        $env:SAMMIE_REPO,
        (Join-Path $PSScriptRoot "..\Sammie-Roto-2"),
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Sammie-Roto-2")
    ) | Where-Object { $_ }
    $SammieRepo = $Candidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "sammie\sammie.py")
    } | Select-Object -First 1
}

if (-not $SammieRepo) {
    throw "Sammie-Roto-2 was not found. Pass -SammieRepo or set SAMMIE_REPO."
}
$SammieRepo = (Resolve-Path -LiteralPath $SammieRepo).Path
$Python = Join-Path $SammieRepo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Sammie-Roto-2 Python environment was not found: $Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $SammieRepo "sammie\sammie.py"))) {
    throw "This is not a Sammie-Roto-2 checkout: $SammieRepo"
}

try {
    $Health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($Health.service -eq "samosa-ae") {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/shutdown" -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null
        Start-Sleep -Milliseconds 500
    } else {
        throw "Port $Port is already used by another service. Choose a different -Port."
    }
} catch {
    if ($_.Exception.Message -like "Port $Port is already used*") { throw }
}

if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Recurse -Force
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "panel\*") -Destination $Target -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "backend") -Destination (Join-Path $Target "backend") -Recurse -Force

$Config = @{
    port = $Port
    repo = $SammieRepo
    python = $Python
    accepted_restricted_models = $false
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $Target "config.json"), $Config, (New-Object System.Text.UTF8Encoding($false)))

foreach ($Version in 11..14) {
    $Key = "HKCU:\Software\Adobe\CSXS.$Version"
    New-Item -Path $Key -Force | Out-Null
    New-ItemProperty -Path $Key -Name PlayerDebugMode -Value "1" -PropertyType String -Force | Out-Null
}

& $Python -m py_compile (Join-Path $Target "backend\service.py")
if ($LASTEXITCODE -ne 0) {
    throw "Installed backend failed Python compilation"
}

Write-Host "Installed Samosa for After Effects"
Write-Host "Extension: $Target"
Write-Host "Restart After Effects, then open Window > Extensions (Legacy) > Samosa."
