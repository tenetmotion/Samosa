[CmdletBinding()]
param(
    [string]$InstallRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$Models,
    [switch]$Complete,
    [switch]$AcceptRestrictedModels
)

$ErrorActionPreference = "Stop"
$Packs = [ordered]@{
    "1" = @{ Name = "SAM2 Base"; Keys = @("Base"); Restricted = $false }
    "2" = @{ Name = "SAM2 Large"; Keys = @("Large"); Restricted = $false }
    "3" = @{ Name = "EfficientTAM"; Keys = @("Efficient"); Restricted = $false }
    "4" = @{ Name = "MatAnyone"; Keys = @("matanyone"); Restricted = $true }
    "5" = @{ Name = "MatAnyone2"; Keys = @("matanyone2"); Restricted = $true }
    "6" = @{ Name = "VideoMaMa"; Keys = @("videomama", "svd_vae"); Restricted = $true }
    "7" = @{ Name = "MiniMax Remover"; Keys = @("minimax_transformer", "minimax_vae"); Restricted = $true }
}

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$RuntimeRoot = Join-Path $InstallRoot "runtime\Sammie-Roto-2"
$Python = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"
$Downloader = Join-Path $InstallRoot "installer\download_models.py"
$ConfigPath = Join-Path $InstallRoot "cep\config.json"

if ((Test-Path -LiteralPath $ConfigPath) -and -not $AcceptRestrictedModels) {
    try {
        $ExistingConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
        $AcceptRestrictedModels = [bool]$ExistingConfig.accepted_restricted_models
    } catch {}
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Samosa runtime is not installed: $Python"
}

$SelectedKeys = @()
$RestrictedSelected = $false
if ($Complete) {
    $SelectedKeys = @("all")
    $RestrictedSelected = $true
} elseif ($Models) {
    $SelectedKeys = @($Models -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $RestrictedSelected = [bool](@($SelectedKeys | Where-Object { $_ -match "^(matanyone|matanyone2|minimax_|videomama|svd_vae)" }).Count)
} else {
    Write-Host "Samosa model manager`n"
    foreach ($Entry in $Packs.GetEnumerator()) {
        $Suffix = if ($Entry.Value.Restricted) { " (noncommercial terms)" } else { "" }
        Write-Host "$($Entry.Key)) $($Entry.Value.Name)$Suffix"
    }
    Write-Host "8) All models (Complete)"
    $Choice = Read-Host "Select one or more packs, separated by commas"
    $Numbers = @($Choice -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($Numbers -contains "8") {
        $SelectedKeys = @("all")
        $RestrictedSelected = $true
    } else {
        foreach ($Number in $Numbers) {
            if (-not $Packs.Contains($Number)) { throw "Unknown selection: $Number" }
            $SelectedKeys += $Packs[$Number].Keys
            $RestrictedSelected = $RestrictedSelected -or $Packs[$Number].Restricted
        }
        $SelectedKeys = @($SelectedKeys | Select-Object -Unique)
    }
}

if (-not $SelectedKeys.Count) { throw "Select at least one model pack." }
if ($RestrictedSelected -and -not $AcceptRestrictedModels) {
    Write-Host ""
    Write-Host "MatAnyone/MatAnyone2 use the S-Lab noncommercial license." -ForegroundColor Yellow
    Write-Host "VideoMaMa uses CC BY-NC 4.0; its SVD VAE dependency has separate Stability AI Community License terms." -ForegroundColor Yellow
    Write-Host "MiniMax Remover has noncommercial terms; review the current model-host license." -ForegroundColor Yellow
    Write-Host "See $InstallRoot\THIRD_PARTY_NOTICES.md for complete links and notices."
    $Acceptance = Read-Host "Type ACCEPT to download these restricted model packs"
    if ($Acceptance -ne "ACCEPT") { throw "Restricted model license terms were not accepted." }
    $AcceptRestrictedModels = $true
}

Push-Location $RuntimeRoot
try {
    & $Python $Downloader --repo $RuntimeRoot --models ($SelectedKeys -join ",")
    if ($LASTEXITCODE -ne 0) { throw "Model download failed with exit code $LASTEXITCODE" }
    if ($RestrictedSelected -and $AcceptRestrictedModels -and (Test-Path -LiteralPath $ConfigPath)) {
        $Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
        $Config | Add-Member -NotePropertyName accepted_restricted_models -NotePropertyValue $true -Force
        [System.IO.File]::WriteAllText($ConfigPath, ($Config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    }
    Write-Host "`nSelected model packs are ready." -ForegroundColor Green
} finally {
    Pop-Location
}
