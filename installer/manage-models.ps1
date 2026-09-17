[CmdletBinding()]
param(
    [string]$InstallRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$Models,
    [switch]$Complete,
    [switch]$Sam31,
    [switch]$AcceptRestrictedModels,
    [switch]$AcceptMetaSamLicense
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
$Sam31Manager = Join-Path $InstallRoot "installer\manage-sam31.ps1"
$ConfigPath = Join-Path $InstallRoot "cep\config.json"
$Sam31Selected = [bool]$Sam31

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
    $Requested = @($Models -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($Requested -contains "SAM3.1") {
        $Sam31Selected = $true
        $Requested = @($Requested | Where-Object { $_ -ne "SAM3.1" })
    }
    $SelectedKeys = $Requested
    $RestrictedSelected = [bool](@($SelectedKeys | Where-Object { $_ -match "^(matanyone|matanyone2|minimax_|videomama|svd_vae)" }).Count)
} elseif (-not $Sam31Selected) {
    Write-Host "Samosa model manager"
    Write-Host ""
    foreach ($Entry in $Packs.GetEnumerator()) {
        $Suffix = if ($Entry.Value.Restricted) { " (noncommercial terms)" } else { "" }
        Write-Host "$($Entry.Key)) $($Entry.Value.Name)$Suffix"
    }
    Write-Host "8) All existing model packs (Complete)"
    Write-Host "9) SAM 3.1 (Meta SAM License; Windows NVIDIA only)"
    $Choice = Read-Host "Select one or more packs, separated by commas"
    $Numbers = @($Choice -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($Numbers -contains "8") {
        $SelectedKeys = @("all")
        $RestrictedSelected = $true
    } else {
        foreach ($Number in $Numbers) {
            if ($Number -eq "9") {
                $Sam31Selected = $true
                continue
            }
            if (-not $Packs.Contains($Number)) { throw "Unknown selection: $Number" }
            $SelectedKeys += $Packs[$Number].Keys
            $RestrictedSelected = $RestrictedSelected -or $Packs[$Number].Restricted
        }
        $SelectedKeys = @($SelectedKeys | Select-Object -Unique)
    }
}

if (-not $SelectedKeys.Count -and -not $Sam31Selected) { throw "Select at least one model pack." }
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

if ($SelectedKeys.Count) {
    Push-Location $RuntimeRoot
    try {
        & $Python $Downloader --repo $RuntimeRoot --models ($SelectedKeys -join ",")
        if ($LASTEXITCODE -ne 0) { throw "Model download failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

if ($Sam31Selected) {
    if (-not $AcceptMetaSamLicense) {
        Write-Host ""
        Write-Host "SAM 3.1 is gated and covered by the Meta SAM License. Access must be accepted at Hugging Face." -ForegroundColor Yellow
        Write-Host "The token is read from HF_TOKEN or hidden input for this process only; it is never saved."
        $MetaAcceptance = Read-Host "Type ACCEPT to acknowledge the Meta SAM License and gated access"
        if ($MetaAcceptance -ne "ACCEPT") { throw "SAM 3.1 license and access terms were not acknowledged." }
        $AcceptMetaSamLicense = $true
    }
    & (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -NoProfile -ExecutionPolicy Bypass -File $Sam31Manager -InstallRoot $InstallRoot -Install -DownloadModel -AcceptMetaSamLicense
    if ($LASTEXITCODE -ne 0) { throw "SAM 3.1 setup failed with exit code $LASTEXITCODE" }
}

if ($RestrictedSelected -and $AcceptRestrictedModels -and (Test-Path -LiteralPath $ConfigPath)) {
    $Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
    $Config | Add-Member -NotePropertyName accepted_restricted_models -NotePropertyValue $true -Force
    [System.IO.File]::WriteAllText($ConfigPath, ($Config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
}
Write-Host ""
Write-Host "Selected model packs are ready." -ForegroundColor Green
