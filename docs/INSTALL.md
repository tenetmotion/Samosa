# Installing Samosa on Windows

Samosa is an After Effects panel and a local bridge to Sammie-Roto-2. Samosa v1.3.0 pins Sammie-Roto-2 v2.4.0 at commit `6f2213dfd8bf15c54ef41b9c9eff7af2cfee1c06` and installs it into an isolated runtime. The EXE does not embed third-party source, model weights, or Hugging Face credentials.

## Requirements

- Windows 10 or 11, x64
- Adobe After Effects 2021 or newer
- A supported Sammie-Roto-2 GPU or CPU configuration
- Internet access during installation or first-use downloads
- A writable per-user install folder; the default is `%LOCALAPPDATA%\Programs\Samosa`

## EXE installation

Download `Samosa-Setup-1.3.0.exe` and its checksum from the [latest GitHub release](https://github.com/tenetmotion/Samosa/releases/latest). Verify the checksum and the Tenet, LLC Authenticode signature with:

~~~powershell
Get-FileHash -Algorithm SHA256 .\Samosa-Setup-1.3.0.exe
Get-AuthenticodeSignature .\Samosa-Setup-1.3.0.exe |
    Select-Object Status, StatusMessage, @{Name="Publisher"; Expression={$_.SignerCertificate.Subject}}
~~~

Choose the backend matching the machine:

| Backend | SAM 3.1 |
| --- | --- |
| NVIDIA CUDA 13.0 or 12.6 | Available |
| Intel XPU or CPU | Unsupported |

Choose an installation mode:

| Mode | Behavior |
| --- | --- |
| Standard | Installs SAM2 Base. Other Sammie packs remain on demand. SAM 3.1 is not installed. |
| Complete | Installs all existing Sammie packs and can prepare SAM 3.1. |
| Custom | Installs selected packs and can prepare SAM 3.1. |

SAM 3.1 is only available in Windows Complete or Custom with NVIDIA CUDA. The installer shows a separate Meta SAM License acknowledgment. Its hidden post-install step installs the pinned source and isolated environment, never requests or consumes a token, and records `weights-pending`. Finish the gated weight download from the Samosa panel or interactive manager.

Restart After Effects and open **Window > Extensions (Legacy) > Samosa**.

## Gated SAM 3.1 weights

1. Open the [facebook/sam3.1 model page](https://huggingface.co/facebook/sam3.1).
2. Accept the displayed Meta terms and request/enable access for the Hugging Face account.
3. Run the interactive manager after the EXE completes:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  "$env:LOCALAPPDATA\Programs\Samosa\installer\manage-models.ps1" `
  -InstallRoot "$env:LOCALAPPDATA\Programs\Samosa" -Sam31
~~~

The manager reads `HF_TOKEN` from the current process or accepts a hidden token prompt. It passes the token only to the download process, removes it afterward, and never writes it to install state, configuration, logs, or command-line arguments. The model manager verifies the pinned revision, expected size, and SHA-256. Re-running it is idempotent.

Gated access is granted to an individual Hugging Face account, not to an organization. Open the model page while signed into the same account that created the token, complete the Meta access form, and wait until access is granted before retrying. A general read token works after that account is approved; a fine-grained token must explicitly allow read access to `facebook/sam3.1`. A `403` stating that the user is not in the authorized list means the account-side access grant is still missing, not that the local Samosa runtime failed.

Inspect SAM 3.1 state without downloading:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  "$env:LOCALAPPDATA\Programs\Samosa\installer\manage-sam31.ps1" `
  -InstallRoot "$env:LOCALAPPDATA\Programs\Samosa" -Status -AcceptMetaSamLicense
~~~

## Existing model packs

Use Samosa's on-demand path or **Start > Samosa > Manage model packs**. Existing model files are downloaded to partial files, checksum-verified, atomically activated, and retained. Existing restricted model acknowledgments remain separate. See [Third-party notices](../THIRD_PARTY_NOTICES.md).

## Updating and uninstalling

Run a newer EXE over the existing installation. Valid pinned source and checkpoints are retained; changed revisions are repaired. Use **Windows Settings > Apps > Installed apps > Samosa > Uninstall** to remove the Samosa root and CEP registration. Exports outside the root are not removed.

## Troubleshooting

For `Starting service`, confirm `runtime\Sammie-Roto-2\.venv\Scripts\python.exe` exists and read `logs\installer.log` plus the service log. For `weights-pending`, confirm Hugging Face access is granted to the token's account, use the interactive manager, and provide `HF_TOKEN` or its hidden prompt. SAM 3.1 rejects CPU and XPU backends.
