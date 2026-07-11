[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [ValidateSet("cu130", "cu126", "xpu", "cpu")]
    [string]$Backend = "cu130",
    [ValidateSet("Standard", "Complete", "Custom")]
    [string]$InstallMode = "Standard",
    [string]$Models = "Base",
    [switch]$AcceptRestrictedModels,
    [int]$Port = 43831,
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$SammieCommit = "129a0a54950d71b535cdcdbd06090c5583e293d9"
$SammieArchiveUrl = "https://github.com/Zarxrax/Sammie-Roto-2/archive/$SammieCommit.zip"
$SammieArchiveSha256 = "71CFC39AC389DA6C138E956881DEA1A811F473F63052FB026B8E24CFE28AF62B"
$AllModelKeys = @(
    "Large", "Base", "Efficient", "matanyone", "matanyone2",
    "minimax_transformer", "minimax_vae", "videomama", "svd_vae"
)
$RestrictedModelKeys = @(
    "matanyone", "matanyone2", "minimax_transformer", "minimax_vae",
    "videomama", "svd_vae"
)

function Get-SafeFullPath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue).TrimEnd("\")
}

function Assert-ChildPath([string]$Root, [string]$Candidate) {
    $RootFull = Get-SafeFullPath $Root
    $CandidateFull = Get-SafeFullPath $Candidate
    if (-not $CandidateFull.StartsWith($RootFull + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes the Samosa install root: $CandidateFull"
    }
}

function Resolve-ModelKeys {
    if ($InstallMode -eq "Complete" -or $Models.Trim().ToLowerInvariant() -eq "all") {
        return @($AllModelKeys)
    }
    $Requested = @($Models -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($InstallMode -eq "Standard" -and -not $Requested.Count) {
        $Requested = @("Base")
    }
    $Unknown = @($Requested | Where-Object { $_ -notin $AllModelKeys })
    if ($Unknown.Count) {
        throw "Unknown model key(s): $($Unknown -join ', ')"
    }
    return @($Requested | Select-Object -Unique)
}

function Test-RestrictedSelection([string[]]$Keys) {
    return [bool](@($Keys | Where-Object { $_ -in $RestrictedModelKeys }).Count)
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
        Write-Host "> $FilePath $($Arguments -join ' ')"
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
        }
    } finally {
        Pop-Location
    }
}

function Stop-SamosaService {
    try {
        $Health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($Health.service -eq "samosa-ae") {
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/shutdown" -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null
            Start-Sleep -Milliseconds 500
        } else {
            throw "Port $Port is occupied by another service."
        }
    } catch {
        if ($_.Exception.Message -like "Port $Port is occupied*") { throw }
    }
}

function Remove-CepRegistration {
    $CepTarget = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.tenet.samosa.roto"
    if (-not (Test-Path -LiteralPath $CepTarget)) { return }
    $Item = Get-Item -LiteralPath $CepTarget -Force
    $Owned = $false
    if ($Item.LinkType -eq "Junction") {
        $Targets = @($Item.Target)
        $Owned = [bool](@($Targets | Where-Object {
            (Get-SafeFullPath $_).StartsWith((Get-SafeFullPath $InstallRoot) + "\", [System.StringComparison]::OrdinalIgnoreCase)
        }).Count)
    } else {
        $Manifest = Join-Path $CepTarget "CSXS\manifest.xml"
        if (Test-Path -LiteralPath $Manifest) {
            $Owned = (Get-Content -Raw -LiteralPath $Manifest) -match 'ExtensionBundleId="com\.tenet\.samosa\.roto"'
        }
    }
    if ($Owned) {
        Remove-Item -LiteralPath $CepTarget -Recurse -Force
    }
}

$InstallRoot = Get-SafeFullPath $InstallRoot
$RuntimeRoot = Join-Path $InstallRoot "runtime\Sammie-Roto-2"
$CepRoot = Join-Path $InstallRoot "cep"
$StatePath = Join-Path $InstallRoot "install-state.json"
$SelectedModelKeys = @(Resolve-ModelKeys)
$IncludesRestrictedModels = Test-RestrictedSelection $SelectedModelKeys

if ($Uninstall) {
    Remove-CepRegistration
    exit 0
}

if ($IncludesRestrictedModels -and -not $AcceptRestrictedModels) {
    throw "Restricted model packs require explicit acceptance of their noncommercial license terms."
}

$Plan = [ordered]@{
    install_root = $InstallRoot
    backend = $Backend
    install_mode = $InstallMode
    models = $SelectedModelKeys
    includes_restricted_models = $IncludesRestrictedModels
    sammie_commit = $SammieCommit
    sammie_archive = $SammieArchiveUrl
    sammie_archive_sha256 = $SammieArchiveSha256
    cep_extension_id = "com.tenet.samosa.roto"
    port = $Port
}

if ($DryRun) {
    $Plan | ConvertTo-Json -Depth 4
    exit 0
}

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$LogDir = Join-Path $InstallRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$TranscriptStarted = $false

try {
    Start-Transcript -Path (Join-Path $LogDir "installer.log") -Append | Out-Null
    $TranscriptStarted = $true
    Write-Host "Installing Samosa $InstallMode mode to $InstallRoot"

    Stop-SamosaService
    Assert-ChildPath $InstallRoot $RuntimeRoot

    $SourceMarker = Join-Path $RuntimeRoot ".samosa-upstream.json"
    $RuntimeCurrent = $false
    if ((Test-Path -LiteralPath (Join-Path $RuntimeRoot "pyproject.toml")) -and (Test-Path -LiteralPath $SourceMarker)) {
        try {
            $Marker = Get-Content -Raw -LiteralPath $SourceMarker | ConvertFrom-Json
            $RuntimeCurrent = $Marker.commit -eq $SammieCommit
        } catch {
            $RuntimeCurrent = $false
        }
    }

    if (-not $RuntimeCurrent) {
        $DownloadsDir = Join-Path $InstallRoot "downloads"
        $ArchivePath = Join-Path $DownloadsDir "Sammie-Roto-2-$SammieCommit.zip"
        $ExtractRoot = Join-Path $DownloadsDir "extract-$SammieCommit"
        New-Item -ItemType Directory -Path $DownloadsDir -Force | Out-Null
        if ((Test-Path -LiteralPath $ArchivePath) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash -ne $SammieArchiveSha256)) {
            Remove-Item -LiteralPath $ArchivePath -Force
        }
        if (-not (Test-Path -LiteralPath $ArchivePath)) {
            Write-Host "Downloading pinned Sammie-Roto-2 source..."
            Invoke-WebRequest -Uri $SammieArchiveUrl -OutFile $ArchivePath -UseBasicParsing
        }
        $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash
        if ($ActualHash -ne $SammieArchiveSha256) {
            throw "Sammie-Roto-2 archive checksum mismatch. Expected $SammieArchiveSha256, got $ActualHash."
        }
        if (Test-Path -LiteralPath $ExtractRoot) { Remove-Item -LiteralPath $ExtractRoot -Recurse -Force }
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractRoot
        $Extracted = Get-ChildItem -LiteralPath $ExtractRoot -Directory | Select-Object -First 1
        if (-not $Extracted -or -not (Test-Path -LiteralPath (Join-Path $Extracted.FullName "pyproject.toml"))) {
            throw "Downloaded Sammie-Roto-2 archive has an unexpected structure."
        }
        if (Test-Path -LiteralPath $RuntimeRoot) { Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force }
        New-Item -ItemType Directory -Path (Split-Path $RuntimeRoot) -Force | Out-Null
        Move-Item -LiteralPath $Extracted.FullName -Destination $RuntimeRoot
        Remove-Item -LiteralPath $ExtractRoot -Recurse -Force
        [System.IO.File]::WriteAllText($SourceMarker, (@{ commit = $SammieCommit; source = $SammieArchiveUrl } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    }

    $PreviousState = $null
    if (Test-Path -LiteralPath $StatePath) {
        try { $PreviousState = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json } catch {}
    }
    $Python = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"
    $UvExe = Join-Path $RuntimeRoot ".uv\uv.exe"
    $NeedsSync = -not (Test-Path -LiteralPath $Python) -or -not $PreviousState -or $PreviousState.backend -ne $Backend -or -not $RuntimeCurrent

    if ($NeedsSync) {
        if (-not (Test-Path -LiteralPath $UvExe)) {
            Write-Host "Installing isolated uv runtime..."
            $env:UV_INSTALL_DIR = Join-Path $RuntimeRoot ".uv"
            $InstallerScript = (Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -UseBasicParsing).Content
            & ([scriptblock]::Create($InstallerScript))
        }
        if (-not (Test-Path -LiteralPath $UvExe)) { throw "uv installation did not produce $UvExe" }
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeRoot ".uv\python"
        $env:UV_CACHE_DIR = Join-Path $RuntimeRoot ".uv\uv_cache"
        Invoke-Native $UvExe @("python", "install", "--no-bin", "3.12") $RuntimeRoot
        Invoke-Native $UvExe @("sync", "--extra", $Backend) $RuntimeRoot
    }

    if (-not (Test-Path -LiteralPath $Python)) { throw "Sammie-Roto-2 Python environment was not created." }
    if ($SelectedModelKeys.Count) {
        $Downloader = Join-Path $InstallRoot "installer\download_models.py"
        Invoke-Native $Python @($Downloader, "--repo", $RuntimeRoot, "--models", ($SelectedModelKeys -join ",")) $RuntimeRoot
    }

    if (-not (Test-Path -LiteralPath (Join-Path $CepRoot "index.html"))) {
        throw "Samosa CEP files were not installed to $CepRoot"
    }
    $CepBackend = Join-Path $CepRoot "backend"
    if (Test-Path -LiteralPath $CepBackend) { Remove-Item -LiteralPath $CepBackend -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $InstallRoot "backend") -Destination $CepBackend -Recurse -Force
    $Config = @{
        port = $Port
        repo = $RuntimeRoot
        python = $Python
        accepted_restricted_models = [bool]$AcceptRestrictedModels
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $CepRoot "config.json"), $Config, (New-Object System.Text.UTF8Encoding($false)))
    Invoke-Native $Python @("-m", "py_compile", (Join-Path $CepRoot "backend\service.py")) $RuntimeRoot

    foreach ($Version in 11..14) {
        $Key = "HKCU:\Software\Adobe\CSXS.$Version"
        New-Item -Path $Key -Force | Out-Null
        New-ItemProperty -Path $Key -Name PlayerDebugMode -Value "1" -PropertyType String -Force | Out-Null
    }

    Remove-CepRegistration
    $CepTarget = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.tenet.samosa.roto"
    New-Item -ItemType Directory -Path (Split-Path $CepTarget) -Force | Out-Null
    New-Item -ItemType Junction -Path $CepTarget -Target $CepRoot | Out-Null

    $InstallState = [ordered]@{
        version = "1.2.0"
        installed_at = (Get-Date).ToUniversalTime().ToString("o")
        backend = $Backend
        install_mode = $InstallMode
        models_requested = $SelectedModelKeys
        sammie_commit = $SammieCommit
        port = $Port
    }
    [System.IO.File]::WriteAllText($StatePath, ($InstallState | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Samosa installation completed successfully."
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
