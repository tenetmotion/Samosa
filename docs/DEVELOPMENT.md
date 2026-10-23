# Development

## Repository layout

- `panel/`: CEP UI, manifest, and ExtendScript bridge
- `backend/`: local Python service
- `tests/`: source contracts and runtime integration tests
- `docs/`: architecture and release procedures
- `install.ps1`: local developer/user installer

## Prerequisites

Install Sammie-Roto-2 separately and verify its desktop application before debugging Samosa. Set `SAMMIE_REPO` to that checkout. Do not install dependencies into the Samosa tree; the bridge deliberately runs in the upstream virtual environment. Optional SAM 3.1 and CorridorKey 2.0 development use separate environments created by `installer/manage-sam31.ps1` and `installer/manage-corridorkey.ps1`; do not merge their dependencies into Sammie's environment.

## Install a development build

```powershell
$env:SAMMIE_REPO = "D:\src\Sammie-Roto-2"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Restart After Effects after manifest changes. Panel runtime logs are written under `%APPDATA%\Samosa`. The service uses port `43831` by default; pass `-Port` if it conflicts with another local process.

The development installer keeps the optional SAM 3.1 environment and checkpoint under `%LOCALAPPDATA%\Programs\Samosa` by default, outside the replaceable CEP folder, so reinstalling panel files does not delete model data. Pass `-InstallRoot` to use another persistent location.

## Tests

The dependency-light suite runs contracts, installer checks, frame-boundary tests, and panel timing tests. The real media fixture runs when PyAV, OpenCV, and NumPy are available:

```powershell
python -m unittest discover -s tests -v
node tests/test_panel_runtime.js
node tests/test_sam3_panel_runtime.js
```

The integration suite uses deterministic mock processing but imports the installed upstream packages:

```powershell
$env:SAMMIE_REPO = "D:\src\Sammie-Roto-2"
& "$env:SAMMIE_REPO\.venv\Scripts\python.exe" -m unittest discover -s tests -v
```

The SAM 3.1 suites use a fake official-predictor surface and do not require gated weights. When access is available, run one additional Windows NVIDIA smoke test: install through the panel, restart After Effects, load a short trimmed shot, exercise Point, Box, Text, and Example prompts, propagate, and confirm downstream matting and export consume the generated masks.

Before a pull request, also open the panel in After Effects, load a trimmed file-backed footage layer, confirm the frame count matches its In/Out interval, add include and exclude prompts, track, run one matting job, cancel one job, export a PNG sequence, and confirm the imported layer begins at the reference In point while preserving positive stretch and transform.

## Compatibility changes

Pin release validation to upstream commits and model revisions in `NOTICE.md`. When an upstream internal API changes, keep compatibility code small and document the supported revision. Never silently download or redistribute gated or restricted model assets from the Samosa repository, and never store an access token in panel configuration or installer state.
