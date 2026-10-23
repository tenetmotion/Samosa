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
    [switch]$IncludeSam31,
    [switch]$AcceptSam31License,
    [int]$Port = 43831,
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$SammieCommit = "6f2213dfd8bf15c54ef41b9c9eff7af2cfee1c06"
$SammieArchiveUrl = "https://github.com/Zarxrax/Sammie-Roto-2/archive/$SammieCommit.zip"
$SammieArchiveSha256 = "829670BBDE1A92445DCF3D6716D94E35729974799B058C5169E17125A906BE59"
$Sam31SourceCommit = "660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7"
$Sam31ModelRevision = "daa63191845a41281374e725f4c9e51c7a824460"
$Sam31Checkpoint = "sam3.1_multiplex.pt"
$Sam31CheckpointBytes = 3502755717
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
$Sam31Supported = $Backend -in @("cu130", "cu126")
$Sam31Requested = [bool]$IncludeSam31

if ($Uninstall) {
    Remove-CepRegistration
    exit 0
}

if ($IncludesRestrictedModels -and -not $AcceptRestrictedModels) {
    throw "Restricted model packs require explicit acceptance of their noncommercial license terms."
}

if ($Sam31Requested -and $InstallMode -eq "Standard") {
    throw "SAM 3.1 is available only from Windows Complete or Custom installation. Standard remains lightweight."
}
if ($Sam31Requested -and -not $Sam31Supported) {
    throw "SAM 3.1 requires a Windows NVIDIA CUDA backend (cu130 or cu126); it is not available for CPU or XPU installs."
}
if ($Sam31Requested -and -not $AcceptSam31License) {
    throw "SAM 3.1 requires explicit acknowledgment of the Meta SAM License and gated model access terms."
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
    sam31_requested = $Sam31Requested
    sam31_supported = $Sam31Supported
    sam31_source_commit = $Sam31SourceCommit
    sam31_model_revision = $Sam31ModelRevision
    sam31_checkpoint = $Sam31Checkpoint
    sam31_checkpoint_bytes = $Sam31CheckpointBytes
    sam31_license_acknowledged = [bool]($Sam31Requested -and $AcceptSam31License)
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
        $RuntimeCurrent = $true
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
            $UvInstallerScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("samosa-uv-install-{0}.ps1" -f ([Guid]::NewGuid().ToString("N")))
            try {
                Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $UvInstallerScriptPath -UseBasicParsing
                & (Join-Path $PSScriptRoot "invoke-utf8-script.ps1") -Path $UvInstallerScriptPath
            }
            finally {
                if (Test-Path -LiteralPath $UvInstallerScriptPath) {
                    Remove-Item -LiteralPath $UvInstallerScriptPath -Force -ErrorAction SilentlyContinue
                }
            }
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

    if ($Sam31Requested) {
        $Sam31Manager = Join-Path $InstallRoot "installer\manage-sam31.ps1"
        Invoke-Native (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Sam31Manager,
            "-InstallRoot", $InstallRoot, "-Backend", $Backend, "-Install",
            "-DownloadModel", "-NonInteractive", "-AcceptMetaSamLicense"
        ) $InstallRoot
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
        sam31_runtime = Join-Path $InstallRoot "runtime\SAM3.1"
        sam31_requested = $Sam31Requested
        environment = @{
            SAMOSA_INSTALL_ROOT = $InstallRoot
            SAMOSA_CORE_RUNTIME = $RuntimeRoot
            SAMOSA_BACKEND = $Backend
        }
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $CepRoot "config.json"), $Config, (New-Object System.Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $CepBackend -Filter "*.py" | ForEach-Object {
        Invoke-Native $Python @("-m", "py_compile", $_.FullName) $RuntimeRoot
    }

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
        version = "1.4.0"
        installed_at = (Get-Date).ToUniversalTime().ToString("o")
        backend = $Backend
        install_mode = $InstallMode
        models_requested = $SelectedModelKeys
        sammie_commit = $SammieCommit
        sammie_archive_sha256 = $SammieArchiveSha256
        sam31 = [ordered]@{
            requested = $Sam31Requested
            supported = $Sam31Supported
            source_commit = $Sam31SourceCommit
            model_revision = $Sam31ModelRevision
            checkpoint = $Sam31Checkpoint
            checkpoint_bytes = $Sam31CheckpointBytes
            checkpoint_sha256 = "0567DEBEEC80BA4AC6369540C6C248025283CB3FF2B92827509E57E2B3541CB6"
            state_file = Join-Path $InstallRoot "runtime\SAM3.1\sam31-state.json"
        }
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
