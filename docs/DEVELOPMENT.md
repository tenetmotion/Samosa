# Development

## Repository layout

- `panel/`: CEP UI, manifest, and ExtendScript bridge
- `backend/`: local Python service
- `tests/`: source contracts and runtime integration tests
- `docs/`: architecture and release procedures
- `install.ps1`: local developer/user installer

## Prerequisites

Install Sammie-Roto-2 separately and verify its desktop application before debugging Samosa. Set `SAMMIE_REPO` to that checkout. Do not install dependencies into the Samosa tree; the bridge deliberately runs in the upstream virtual environment.

## Install a development build

```powershell
$env:SAMMIE_REPO = "D:\src\Sammie-Roto-2"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Restart After Effects after manifest changes. Panel runtime logs are written under `%APPDATA%\Samosa`. The service uses port `43831` by default; pass `-Port` if it conflicts with another local process.

## Tests

Source contracts have no third-party Python requirements:

```powershell
python -m unittest discover -s tests -p test_contracts.py -v
```

The integration suite uses deterministic mock processing but imports the installed upstream packages:

```powershell
$env:SAMMIE_REPO = "D:\src\Sammie-Roto-2"
& "$env:SAMMIE_REPO\.venv\Scripts\python.exe" -m unittest discover -s tests -v
```

Before a pull request, also open the panel in After Effects, load file-backed footage, add include and exclude points, track, run one matting job, cancel one job, export a PNG sequence, and confirm the imported layer preserves timing and transform.

## Compatibility changes

Pin release validation to an upstream commit in `NOTICE.md`. When Sammie-Roto-2 changes an internal API, keep compatibility code small and document the first and last supported upstream versions. Never silently download or redistribute restricted model assets from the Samosa repository.
