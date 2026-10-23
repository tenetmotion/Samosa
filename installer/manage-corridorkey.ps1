[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [ValidateSet("cu130", "cu126")][string]$Backend = "cu130",
    [switch]$Install,
    [switch]$DownloadModels,
    [switch]$AcceptCorridorKeyLicense,
    [switch]$DryRun,
    [switch]$Status,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$SourceCommit = "485f5e9ab759ead6138cc990776184b59d6ea971"
$ArchiveUrl = "https://codeload.github.com/nikopueringer/CorridorKey2.0/zip/$SourceCommit"
$ArchiveSha256 = "9DB1ABA4597C739B5C4603D463EC2760984B3A3B74D2B320D1E7D07F2E329B07"
$ModelRevision = "cf44a8db3f024bb49fd59daa408848ccf05f7477"
$ModelBytes = 2661698720
$ModelSha256 = "C43596DE98CC8866D577C8A9F9EA239ACDF3F7A130FF24E25DB4375A50590436"
$StatsBytes = 18864
$StatsSha256 = "924783CEDDD293FD470126332CE328166FAEA6C4C8D2F36402D5B95187166AF3"
$EnvironmentRevision = 1
$TorchVersion = "2.9.1"
$TorchvisionVersion = "0.24.1"

function Invoke-Native([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
        Write-Host "> $FilePath $($Arguments -join ' ')"
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $FilePath" }
    } finally { Pop-Location }
}

function Test-ExactFile([string]$Path, [long]$Bytes, [string]$Sha256) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -ne $Bytes) { return $false }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -eq $Sha256
}

function Get-AssetStatus([string]$Root) {
    $Weights = Join-Path $Root "weights"
    $ModelOk = Test-ExactFile (Join-Path $Weights "corridorkey_v2.safetensors") $ModelBytes $ModelSha256
    $StatsOk = Test-ExactFile (Join-Path $Weights "foundation_stats.safetensors") $StatsBytes $StatsSha256
    $RvmOk = Test-Path -LiteralPath (Join-Path $Root "vfms\rvm\pretrain_rvm_small16_256_204031069.npz") -PathType Leaf
    $MogeOk = Test-Path -LiteralPath (Join-Path $Root "vfms\moge\model.pt") -PathType Leaf
    $CradioOk = Test-Path -LiteralPath (Join-Path $Root "vfms\cradio\C-RADIOv4-SO400M")
    return ($ModelOk -and $StatsOk -and $RvmOk -and $MogeOk -and $CradioOk)
}

function Write-State([string]$Root, [bool]$SourceInstalled, [bool]$RuntimeInstalled, [bool]$AssetsAvailable) {
    $State = [ordered]@{
        source_commit = $SourceCommit; source_archive_sha256 = $ArchiveSha256
        model_revision = $ModelRevision; environment_revision = $EnvironmentRevision
        torch = $TorchVersion; torchvision = $TorchvisionVersion
        source_installed = $SourceInstalled; runtime_installed = $RuntimeInstalled
        weights_available = $AssetsAvailable
        status = if ($SourceInstalled -and $RuntimeInstalled -and $AssetsAvailable) { "ready" } else { "not-installed" }
        platform = "windows-nvidia"; updated_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $Path = Join-Path $Root "corridorkey-state.json"
    [System.IO.File]::WriteAllText($Path, ($State | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    return $State
}

if (-not $AcceptCorridorKeyLicense) {
    throw "CorridorKey 2.0 requires explicit acceptance of its separate Corridor Key Licence. Commercial use requires written permission from Corridor."
}
if ($env:OS -ne "Windows_NT") { throw "This CorridorKey integration currently supports Windows NVIDIA systems only." }

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd("\")
$Root = Join-Path $InstallRoot "runtime\CorridorKey2.0"
$CoreRuntime = if ($env:SAMOSA_CORE_RUNTIME) { $env:SAMOSA_CORE_RUNTIME } else { Join-Path $InstallRoot "runtime\Sammie-Roto-2" }
$UvExe = Join-Path $CoreRuntime ".uv\uv.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$SourceMarker = Join-Path $Root ".samosa-corridorkey-source.json"
$EnvMarker = Join-Path $Root ".samosa-corridorkey-env.json"

if ($DryRun) {
    [ordered]@{
        platform = "windows-nvidia"; backend = $Backend
        source_commit = $SourceCommit; source_archive_sha256 = $ArchiveSha256
        model_revision = $ModelRevision
        checkpoint_bytes = $ModelBytes; checkpoint_sha256 = $ModelSha256
        stats_bytes = $StatsBytes; stats_sha256 = $StatsSha256
        environment_revision = $EnvironmentRevision
        torch = $TorchVersion; torchvision = $TorchvisionVersion
        license_required = $true; model_assets_embedded = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

if (-not $Install -and -not $Status) { $Install = $true }
$SourceCurrent = $false
if ((Test-Path -LiteralPath (Join-Path $Root "infer.py")) -and (Test-Path -LiteralPath $SourceMarker)) {
    try { $SourceCurrent = (Get-Content -Raw -LiteralPath $SourceMarker | ConvertFrom-Json).commit -eq $SourceCommit } catch { $SourceCurrent = $false }
}

if ($Install -and -not $SourceCurrent) {
    if (-not (Test-Path -LiteralPath $UvExe)) { throw "Install Samosa's core runtime before CorridorKey 2.0." }
    $Downloads = Join-Path $InstallRoot "downloads"
    $Archive = Join-Path $Downloads "corridorkey-$SourceCommit.zip"
    $Extract = Join-Path $Downloads "extract-corridorkey-$SourceCommit"
    New-Item -ItemType Directory -Path $Downloads -Force | Out-Null
    if ((Test-Path -LiteralPath $Archive) -and (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash -ne $ArchiveSha256) { Remove-Item -LiteralPath $Archive -Force }
    if (-not (Test-Path -LiteralPath $Archive)) {
        Write-Host "Downloading pinned CorridorKey source..."
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $Archive -UseBasicParsing
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash
    if ($Actual -ne $ArchiveSha256) { throw "CorridorKey source checksum mismatch. Expected $ArchiveSha256, got $Actual." }
    if (Test-Path -LiteralPath $Extract) { Remove-Item -LiteralPath $Extract -Recurse -Force }
    Expand-Archive -LiteralPath $Archive -DestinationPath $Extract
    $Source = Get-ChildItem -LiteralPath $Extract -Directory | Select-Object -First 1
    if (-not $Source -or -not (Test-Path -LiteralPath (Join-Path $Source.FullName "infer.py"))) { throw "Downloaded CorridorKey archive has an unexpected structure." }
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    Get-ChildItem -LiteralPath $Root -Force | Where-Object { $_.Name -notin @(".venv", "weights", "vfms") } | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $Source.FullName "*") -Destination $Root -Recurse -Force
    Remove-Item -LiteralPath $Extract -Recurse -Force
    [System.IO.File]::WriteAllText($SourceMarker, (@{ commit = $SourceCommit; source = $ArchiveUrl; sha256 = $ArchiveSha256 } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    $SourceCurrent = $true
}

$EnvCurrent = $false
if ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $EnvMarker)) {
    try {
        $Existing = Get-Content -Raw -LiteralPath $EnvMarker | ConvertFrom-Json
        $EnvCurrent = $Existing.commit -eq $SourceCommit -and [int]$Existing.environment_revision -eq $EnvironmentRevision -and $Existing.torch -eq $TorchVersion -and $Existing.torchvision -eq $TorchvisionVersion
    } catch { $EnvCurrent = $false }
}
if ($Install -and -not $EnvCurrent) {
    Write-Host "Installing CorridorKey Python runtime..."
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $CoreRuntime ".uv\python"
    $env:UV_CACHE_DIR = Join-Path $CoreRuntime ".uv\uv_cache"
    Invoke-Native $UvExe @("python", "install", "--no-bin", "3.12") $Root
    if (-not (Test-Path -LiteralPath $Python)) { Invoke-Native $UvExe @("venv", "--python", "3.12", (Join-Path $Root ".venv")) $Root }
    Invoke-Native $UvExe @("pip", "install", "--python", $Python, "--index-url", "https://download.pytorch.org/whl/cu128", "torch==$TorchVersion", "torchvision==$TorchvisionVersion") $Root
    Invoke-Native $UvExe @("pip", "install", "--python", $Python, "-r", (Join-Path $Root "requirements.txt")) $Root
    [System.IO.File]::WriteAllText($EnvMarker, (@{ commit = $SourceCommit; environment_revision = $EnvironmentRevision; torch = $TorchVersion; torchvision = $TorchvisionVersion; torch_index = "cu128" } | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    $EnvCurrent = $true
}

$AssetsAvailable = Get-AssetStatus $Root
if ($DownloadModels -and -not $AssetsAvailable) {
    Write-Host "Downloading CorridorKey model and pinned foundation assets..."
    Invoke-Native $Python @((Join-Path $Root "scripts\download_weights.py"), "--package_root", $Root, "--corridorkey_revision", $ModelRevision) $Root
    $AssetsAvailable = Get-AssetStatus $Root
    if (-not $AssetsAvailable) { throw "CorridorKey model assets failed checksum or completeness validation." }
}

if ($Install -and $EnvCurrent -and $AssetsAvailable) {
    Write-Host "Validating CorridorKey runtime..."
    Invoke-Native $Python @("-c", "import torch; from corridorkey.runtime import run_inference; assert torch.cuda.is_available(), 'CorridorKey requires NVIDIA CUDA'; print('CorridorKey runtime validated')") $Root
}

$State = Write-State $Root $SourceCurrent $EnvCurrent $AssetsAvailable
$State | ConvertTo-Json -Depth 4
if ($Install -and $State.status -ne "ready") { throw "CorridorKey setup is incomplete." }
