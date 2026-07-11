# Installing Samosa on macOS

Samosa 1.2.0 provides an online PKG for Apple Silicon and Intel Macs. The package installs the After Effects CEP panel and creates a writable per-user Sammie-Roto-2 runtime.

## Requirements

- macOS 13 or newer
- Adobe After Effects 2021 or newer
- Internet access during runtime and model installation
- Approximately 8 GB of free space for Standard installation; Complete requires substantially more

Apple Silicon uses PyTorch Metal Performance Shaders when available. Intel Macs use CPU processing and will be considerably slower. The package contains scripts and panel files for both `arm64` and `x86_64`; Python and PyTorch are downloaded for the current Mac during setup.

## Install

1. Download `Samosa-1.2.0-macOS.pkg` and `Samosa-1.2.0-macOS-SHA256.txt` from the [latest release](https://github.com/tenetmotion/Samosa/releases/latest).
2. In Terminal, verify the package:

```bash
cd ~/Downloads
shasum -a 256 Samosa-1.2.0-macOS.pkg
cat Samosa-1.2.0-macOS-SHA256.txt
```

3. Open the PKG and follow Installer. This initial package is unsigned. If Gatekeeper blocks it, open **System Settings > Privacy & Security**, confirm that the package came from the Samosa GitHub release, then choose **Open Anyway**.
4. Keep the Mac online while the installer downloads the pinned runtime, Python dependencies, and SAM2 Base checkpoint.
5. Restart After Effects and open **Window > Extensions (Legacy) > Samosa**.

The package installs Standard mode. Additional models download when first selected in Samosa. To pre-download packs, open **Applications > Samosa > Manage Model Packs.command**. Choosing `A` there is equivalent to the Windows Complete mode; selecting individual numbers is equivalent to Custom mode.

## Installation locations

User-writable runtime, environment, and models:

```text
~/Library/Application Support/Samosa
```

After Effects CEP registration:

```text
~/Library/Application Support/Adobe/CEP/extensions/com.tenet.samosa.roto
```

Installer resources and Finder commands:

```text
/Applications/Samosa
```

Logs:

```text
~/Library/Logs/Samosa
~/Library/Application Support/Samosa/logs/installer.log
```

## Models

Standard installs SAM2 Base. Missing packs use the same registry-driven process as Windows: download to `.part`, verify the registry checksum, atomically activate, and retain the checkpoint locally. Restricted matting/removal packs require license confirmation. See [Model installation](MODELS.md) and [Third-party notices](../THIRD_PARTY_NOTICES.md).

## Updating and uninstalling

Run a newer Samosa PKG over the existing installation. Matching runtime revisions and valid model checkpoints are retained.

To uninstall, open **Applications > Samosa > Uninstall Samosa.command**. It removes the user runtime, downloaded models, logs, CEP registration, and `/Applications/Samosa` after requesting administrator approval for the last location.

## Troubleshooting

### Panel remains on Starting processing service

- Confirm `~/Library/Application Support/Samosa/runtime/Sammie-Roto-2/.venv/bin/python` exists.
- Read `~/Library/Logs/Samosa/service.log` and `panel-runtime.log`.
- Read `~/Library/Application Support/Samosa/logs/installer.log` for download or dependency failures.
- Confirm another application is not using TCP port `43831`.

### Extension does not appear

- Restart After Effects after installation.
- Confirm Legacy Extensions are enabled by the installed CEP debug setting.
- Confirm the CEP registration path above is a symbolic link to the Samosa user runtime.

### Processing is slow

Check the service log for `Using device: mps` on Apple Silicon. `Using device: cpu` is expected on Intel Macs and is substantially slower. Some model operations can fall back from MPS to CPU when PyTorch does not implement an operation on Metal.
