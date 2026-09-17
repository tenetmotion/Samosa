# Installing Samosa on macOS

Samosa 1.3.0 provides an online PKG for Apple Silicon and Intel Macs. It installs a per-user Sammie-Roto-2 v2.4.0 runtime.

## Requirements

- macOS 13 or newer
- Adobe After Effects 2021 or newer
- Internet access during runtime and model installation
- A writable user account

Apple Silicon uses MPS where available; Intel Macs use CPU processing. The package supports `arm64` and `x86_64`.

## SAM 3.1 support boundary

SAM 3.1 is unsupported on macOS in this release. It requires the Windows NVIDIA CUDA runtime. The macOS bootstrap and model manager explicitly report `unsupported` and do not install a nonfunctional SAM 3.1 environment. SAM2, EfficientTAM, matting, and removal workflows remain available.

## Install

1. Download `Samosa-1.3.0-macOS.pkg` and its checksum from the [latest release](https://github.com/tenetmotion/Samosa/releases/latest).
2. Verify it in Terminal:

~~~bash
shasum -a 256 ~/Downloads/Samosa-1.3.0-macOS.pkg
cat ~/Downloads/Samosa-1.3.0-macOS-SHA256.txt
~~~

3. Open the PKG and approve it in **System Settings > Privacy & Security** if Gatekeeper blocks the unsigned package.
4. Keep the Mac online while the pinned runtime and SAM2 Base checkpoint are installed.
5. Restart After Effects and open **Window > Extensions (Legacy) > Samosa**.

Additional existing model packs are available through **Applications > Samosa > Manage Model Packs.command**. There is no SAM 3.1 option on macOS.

## Locations

Runtime and models: `~/Library/Application Support/Samosa`

CEP registration: `~/Library/Application Support/Adobe/CEP/extensions/com.tenet.samosa.roto`

Logs: `~/Library/Logs/Samosa` and `~/Library/Application Support/Samosa/logs`

## Updating and uninstalling

Run a newer PKG over the existing install. Matching valid revisions are retained. Use **Applications > Samosa > Uninstall Samosa.command** to remove the user runtime, models, logs, and CEP registration.

## Troubleshooting

Confirm `~/Library/Application Support/Samosa/runtime/Sammie-Roto-2/.venv/bin/python` exists. Read `~/Library/Logs/Samosa/service.log` and the installer log. `mps` is expected on Apple Silicon; `cpu` is expected on Intel.
