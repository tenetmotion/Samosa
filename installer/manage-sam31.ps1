[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [ValidateSet("cu130", "cu126")]
    [string]$Backend = "cu130",
    [switch]$Install,
    [switch]$DownloadModel,
    [switch]$AcceptMetaSamLicense,
    [switch]$DryRun,
    [switch]$Status,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Sam31SourceCommit = "660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7"
$Sam31ArchiveUrl = "https://github.com/facebookresearch/sam3/archive/$Sam31SourceCommit.zip"
$Sam31ArchiveSha256 = "CF25FAAAA0B83B8F5F7327B63E5DAA7E27EDED52EDA12BB4ADC01C9CF3D16090"
$Sam31ModelRevision = "daa63191845a41281374e725f4c9e51c7a824460"
$Sam31Checkpoint = "sam3.1_multiplex.pt"
$Sam31CheckpointBytes = 3502755717
$Sam31CheckpointSha256 = "0567DEBEEC80BA4AC6369540C6C248025283CB3FF2B92827509E57E2B3541CB6"
$Sam31EnvironmentRevision = 4
$Sam31TorchVersion = "2.10.0"
$Sam31TorchvisionVersion = "0.25.0"
$Sam31EinopsVersion = "0.8.1"
$Sam31TritonWindowsVersion = "3.6.0.post26"
$Sam31PycocotoolsVersion = "2.0.10"
$Sam31PsutilVersion = "7.2.1"

function Get-SafeFullPath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue).TrimEnd("\")
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

function Get-CheckpointPath([string]$Root) {
    return Join-Path (Join-Path $Root "runtime\SAM3.1\models") $Sam31Checkpoint
}

function Test-Checkpoint([string]$Root) {
    $Path = Get-CheckpointPath $Root
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -ne $Sam31CheckpointBytes) { return $false }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -eq $Sam31CheckpointSha256
}

function Read-HuggingFaceToken {
    if ($NonInteractive) { return $null }
    if ($env:HF_TOKEN) { return $env:HF_TOKEN }
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        $Secure = Read-Host "Hugging Face token (hidden; never saved)" -AsSecureString
        $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
        try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr) }
    }
    return $null
}

function Write-State([string]$Root, [bool]$SourceInstalled, [bool]$WeightsAvailable, [string]$StatusValue) {
    $StatePath = Join-Path $Root "runtime\SAM3.1\sam31-state.json"
    New-Item -ItemType Directory -Path (Split-Path $StatePath) -Force | Out-Null
    $State = [ordered]@{
        source_commit = $Sam31SourceCommit
        source_archive_sha256 = $Sam31ArchiveSha256
        model_revision = $Sam31ModelRevision
        checkpoint = $Sam31Checkpoint
        checkpoint_bytes = $Sam31CheckpointBytes
        checkpoint_sha256 = $Sam31CheckpointSha256
        environment_revision = $Sam31EnvironmentRevision
        torch = $Sam31TorchVersion
        torchvision = $Sam31TorchvisionVersion
        einops = $Sam31EinopsVersion
        triton_windows = $Sam31TritonWindowsVersion
        pycocotools = $Sam31PycocotoolsVersion
        psutil = $Sam31PsutilVersion
        source_installed = $SourceInstalled
        weights_available = $WeightsAvailable
        status = $StatusValue
        platform = "windows-nvidia"
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    [System.IO.File]::WriteAllText($StatePath, ($State | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    return $State
}

$InstallRoot = Get-SafeFullPath $InstallRoot
$Sam31Root = Join-Path $InstallRoot "runtime\SAM3.1"
$SourceMarker = Join-Path $Sam31Root ".samosa-sam31-source.json"
$CoreRuntime = if ($env:SAMOSA_CORE_RUNTIME) { Get-SafeFullPath $env:SAMOSA_CORE_RUNTIME } else { Join-Path $InstallRoot "runtime\Sammie-Roto-2" }
$UvExe = Join-Path $CoreRuntime ".uv\uv.exe"
$Python = Join-Path $Sam31Root ".venv\Scripts\python.exe"
$Downloader = Join-Path $InstallRoot "installer\sam31_download.py"
$EnvMarker = Join-Path $Sam31Root ".samosa-sam31-env.json"
$CheckpointPath = Get-CheckpointPath $InstallRoot

if (-not $AcceptMetaSamLicense) {
    throw "SAM 3.1 requires explicit acknowledgment of the Meta SAM License and gated model access terms."
}

if ($DryRun) {
    [ordered]@{
        platform = "windows-nvidia"
        backend = $Backend
        source_commit = $Sam31SourceCommit
        source_archive_sha256 = $Sam31ArchiveSha256
        model_revision = $Sam31ModelRevision
        checkpoint = $Sam31Checkpoint
        checkpoint_bytes = $Sam31CheckpointBytes
        checkpoint_sha256 = $Sam31CheckpointSha256
        environment_revision = $Sam31EnvironmentRevision
        torch = $Sam31TorchVersion
        torchvision = $Sam31TorchvisionVersion
        einops = $Sam31EinopsVersion
        triton_windows = $Sam31TritonWindowsVersion
        pycocotools = $Sam31PycocotoolsVersion
        psutil = $Sam31PsutilVersion
        license_required = $true
        token_persisted = $false
        idempotent_markers = @($SourceMarker, $EnvMarker, (Join-Path $Sam31Root "sam31-state.json"))
    } | ConvertTo-Json -Depth 4
    exit 0
}

if (-not $Install -and -not $Status) { $Install = $true }
if (-not $Status -and -not (Test-Path -LiteralPath $UvExe)) {
    throw "Samosa's core uv runtime is missing. Install the core runtime before setting up SAM 3.1."
}

$SourceCurrent = $false
if ((Test-Path -LiteralPath (Join-Path $Sam31Root "pyproject.toml")) -and (Test-Path -LiteralPath $SourceMarker)) {
    try { $SourceCurrent = (Get-Content -Raw -LiteralPath $SourceMarker | ConvertFrom-Json).commit -eq $Sam31SourceCommit } catch { $SourceCurrent = $false }
}

if ($Install -and -not $SourceCurrent) {
    $DownloadsDir = Join-Path $InstallRoot "downloads"
    $ArchivePath = Join-Path $DownloadsDir "sam3-$Sam31SourceCommit.zip"
    $ExtractRoot = Join-Path $DownloadsDir "extract-sam3-$Sam31SourceCommit"
    New-Item -ItemType Directory -Path $DownloadsDir -Force | Out-Null
    if ((Test-Path -LiteralPath $ArchivePath) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash -ne $Sam31ArchiveSha256)) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }
    if (-not (Test-Path -LiteralPath $ArchivePath)) {
        Write-Host "Downloading pinned SAM 3.1 source..."
        Invoke-WebRequest -Uri $Sam31ArchiveUrl -OutFile $ArchivePath -UseBasicParsing
    }
    $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash
    if ($ActualHash -ne $Sam31ArchiveSha256) {
        throw "SAM 3.1 source archive checksum mismatch. Expected $Sam31ArchiveSha256, got $ActualHash."
    }
    if (Test-Path -LiteralPath $ExtractRoot) { Remove-Item -LiteralPath $ExtractRoot -Recurse -Force }
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractRoot
    $Extracted = Get-ChildItem -LiteralPath $ExtractRoot -Directory | Select-Object -First 1
    if (-not $Extracted -or -not (Test-Path -LiteralPath (Join-Path $Extracted.FullName "pyproject.toml"))) {
        throw "Downloaded SAM 3.1 archive has an unexpected structure."
    }
    $ExistingModels = Join-Path $Sam31Root "models"
    $PreservedModels = Join-Path $DownloadsDir ("preserved-sam31-models-" + [Guid]::NewGuid().ToString("N"))
    if (Test-Path -LiteralPath $ExistingModels) {
        Move-Item -LiteralPath $ExistingModels -Destination $PreservedModels
    }
    try {
        if (Test-Path -LiteralPath $Sam31Root) { Remove-Item -LiteralPath $Sam31Root -Recurse -Force }
        New-Item -ItemType Directory -Path (Split-Path $Sam31Root) -Force | Out-Null
        Move-Item -LiteralPath $Extracted.FullName -Destination $Sam31Root
        if (Test-Path -LiteralPath $PreservedModels) {
            Move-Item -LiteralPath $PreservedModels -Destination (Join-Path $Sam31Root "models")
        }
    } catch {
        if (Test-Path -LiteralPath $PreservedModels) {
            New-Item -ItemType Directory -Path $Sam31Root -Force | Out-Null
            Move-Item -LiteralPath $PreservedModels -Destination (Join-Path $Sam31Root "models")
        }
        throw
    }
    Remove-Item -LiteralPath $ExtractRoot -Recurse -Force
    [System.IO.File]::WriteAllText($SourceMarker, (@{ commit = $Sam31SourceCommit; source = $Sam31ArchiveUrl; sha256 = $Sam31ArchiveSha256 } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    $SourceCurrent = $true
}

$EnvCurrent = $false
if ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $EnvMarker)) {
    try {
        $ExistingEnvironment = Get-Content -Raw -LiteralPath $EnvMarker | ConvertFrom-Json
        $EnvCurrent = (
            $ExistingEnvironment.commit -eq $Sam31SourceCommit -and
            [int]$ExistingEnvironment.environment_revision -eq $Sam31EnvironmentRevision -and
            $ExistingEnvironment.torch -eq $Sam31TorchVersion -and
            $ExistingEnvironment.torchvision -eq $Sam31TorchvisionVersion -and
            $ExistingEnvironment.einops -eq $Sam31EinopsVersion -and
            $ExistingEnvironment.triton_windows -eq $Sam31TritonWindowsVersion -and
            $ExistingEnvironment.pycocotools -eq $Sam31PycocotoolsVersion -and
            $ExistingEnvironment.psutil -eq $Sam31PsutilVersion
        )
    } catch { $EnvCurrent = $false }
}
if ($Install -and $SourceCurrent -and -not $EnvCurrent) {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $CoreRuntime ".uv\python"
    $env:UV_CACHE_DIR = Join-Path $CoreRuntime ".uv\uv_cache"
    Invoke-Native $UvExe @("python", "install", "--no-bin", "3.12") $Sam31Root
    if (-not (Test-Path -LiteralPath $Python)) {
        Invoke-Native $UvExe @("venv", "--python", "3.12", (Join-Path $Sam31Root ".venv")) $Sam31Root
    }
    Invoke-Native $UvExe @("pip", "install", "--python", $Python, "--index-url", "https://download.pytorch.org/whl/cu126", "torch==$Sam31TorchVersion", "torchvision==$Sam31TorchvisionVersion") $Sam31Root
    Invoke-Native $UvExe @("pip", "install", "--python", $Python, "--no-deps", "-e", $Sam31Root) $Sam31Root
    Invoke-Native $UvExe @("pip", "install", "--python", $Python, "timm>=1.0.17", "numpy>=1.26,<2", "tqdm", "ftfy==6.1.1", "regex", "iopath>=0.1.10", "typing_extensions", "huggingface_hub", "einops==$Sam31EinopsVersion", "triton-windows==$Sam31TritonWindowsVersion", "pycocotools==$Sam31PycocotoolsVersion", "psutil==$Sam31PsutilVersion") $Sam31Root
    Invoke-Native $Python @("-c", "import einops, psutil, pycocotools, torch, torchvision, triton; from sam3.model_builder import build_sam3_multiplex_video_predictor; from sam3.model.edt import edt_triton; assert torch.cuda.is_available(), 'SAM 3.1 requires an available NVIDIA CUDA device'; _probe = torch.zeros((1, 4, 4), dtype=torch.bool, device='cuda'); edt_triton(_probe); torch.cuda.synchronize(); print('SAM 3.1 runtime and Triton kernel validated')") $Sam31Root
    [System.IO.File]::WriteAllText($EnvMarker, (@{ commit = $Sam31SourceCommit; environment_revision = $Sam31EnvironmentRevision; python = $Python; torch = $Sam31TorchVersion; torchvision = $Sam31TorchvisionVersion; einops = $Sam31EinopsVersion; triton_windows = $Sam31TritonWindowsVersion; pycocotools = $Sam31PycocotoolsVersion; psutil = $Sam31PsutilVersion; torch_index = "cu126"; numpy = ">=1.26,<2" } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
}

if ($Status) {
    if (Test-Path -LiteralPath (Join-Path $Sam31Root "sam31-state.json")) { Get-Content -Raw -LiteralPath (Join-Path $Sam31Root "sam31-state.json") }
    else { Write-State $InstallRoot $SourceCurrent (Test-Checkpoint $InstallRoot) "not-installed" | ConvertTo-Json -Depth 4 }
    exit 0
}

$WeightsAvailable = Test-Checkpoint $InstallRoot
if ($DownloadModel -and -not $WeightsAvailable) {
    $Token = Read-HuggingFaceToken
    if ($Token) {
        try {
            $PreviousToken = $env:HF_TOKEN
            $env:HF_TOKEN = $Token
            Invoke-Native $Python @($Downloader, "--destination", $CheckpointPath) $Sam31Root
        } finally {
            if ($null -eq $PreviousToken) { Remove-Item Env:HF_TOKEN -ErrorAction SilentlyContinue } else { $env:HF_TOKEN = $PreviousToken }
            $Token = $null
        }
        $WeightsAvailable = Test-Checkpoint $InstallRoot
    } else {
        Write-Host "SAM 3.1 source/runtime installed; gated weights remain pending until HF_TOKEN or hidden token input is supplied."
    }
}

$StatusValue = if ($WeightsAvailable) { "ready" } elseif ($SourceCurrent) { "weights-pending" } else { "not-installed" }
Write-State $InstallRoot $SourceCurrent $WeightsAvailable $StatusValue | ConvertTo-Json -Depth 4
