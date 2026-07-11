# Installing Samosa on Windows

Samosa is an After Effects panel and a local bridge to Sammie-Roto-2. Install Sammie-Roto-2 first; Samosa does not bundle Python, model weights, or GPU libraries.

Samosa v1.0.0 is Windows-only. A macOS installer and runtime adaptation are planned after the Windows launch.

## Requirements

- Windows 10 or 11
- Adobe After Effects 2021 or newer
- A supported GPU or CPU configuration for Sammie-Roto-2
- Internet access during the initial Sammie-Roto-2 and model installation
- A folder where your Windows account has write permission; do not install under `Program Files`

## 1. Install Sammie-Roto-2

Download the current source from the [Sammie-Roto-2 repository](https://github.com/Zarxrax/Sammie-Roto-2) and extract it, or clone it:

```powershell
git clone https://github.com/Zarxrax/Sammie-Roto-2.git
cd .\Sammie-Roto-2
```

Run the upstream installer and select the option matching your hardware:

```powershell
.\install.bat
```

The installer creates a local `.venv` and downloads the selected PyTorch runtime. Verify the standalone application before installing Samosa:

```powershell
.\run_sammie.bat
```

Open a short clip and confirm that the application starts and can load footage. You may then close it; Samosa uses the same environment and checkpoints through its own local service.

## 2. Install the Samosa panel

Download this repository's source and extract it to a permanent folder, or clone it:

```powershell
git clone https://github.com/tenetmotion/Samosa.git
cd .\Samosa
```

In PowerShell, open that folder and pass the full Sammie-Roto-2 path to the installer:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SammieRepo "D:\Apps\Sammie-Roto-2"
```

You can instead set `SAMMIE_REPO` and omit the parameter:

```powershell
$env:SAMMIE_REPO = "D:\Apps\Sammie-Roto-2"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer:

- verifies the upstream checkout and `.venv`
- installs `com.tenet.samosa.roto` under `%APPDATA%\Adobe\CEP\extensions`
- writes a machine-local `config.json`
- enables CEP debug mode for supported Adobe hosts
- compiles the Python bridge as a startup check

Restart After Effects, then open **Window > Extensions (Legacy) > Samosa**.

## 3. First-run verification

1. Create or open a composition containing imported footage.
2. Select one footage layer whose source is a local file.
3. Open Samosa and choose **Load selection**.
4. Wait for the footer to report that the GPU service is connected and the viewer to display the clip.
5. Add one Include point. The first point may take longer while the selected model initializes or downloads.

## Model licenses

Samosa is GPL-3.0, but some optional engines are noncommercial. MatAnyone uses the S-Lab noncommercial license; VideoMaMa and MiniMax Remover use CC BY-NC 4.0. Installing a model does not grant commercial-use rights. Read [Third-party notices](../THIRD_PARTY_NOTICES.md) before production or commercial use.

## Updating

Pull or extract the new Samosa source over a clean folder, then rerun `install.ps1` with the same Sammie-Roto-2 path. Restart After Effects after every panel update.

Update Sammie-Roto-2 using its upstream instructions. Because Samosa integrates with upstream Python modules, review Samosa release notes before moving to a new incompatible upstream version.

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

This removes only the Samosa CEP extension. It does not delete Sammie-Roto-2, checkpoints, exports, or footage.

## Troubleshooting

### The panel stays on Starting service

- Confirm `Sammie-Roto-2\.venv\Scripts\python.exe` exists.
- Rerun `install.ps1` with the correct `-SammieRepo` path.
- Read `%APPDATA%\Samosa\service.log` and `%APPDATA%\Samosa\panel-runtime.log`.
- If port `43831` is occupied, reinstall with another port: `-Port 43832`.

### Load selection reports no compatible layer

Select exactly one AV footage layer backed by a local media file. Solids, adjustment layers, nested compositions, generated layers, and offline media are not accepted by the current bridge.

### More than one Samosa entry appears

The active release uses extension ID `com.tenet.samosa.roto`. Remove older prototypes from `%APPDATA%\Adobe\CEP\extensions` only after confirming they are no longer needed.
