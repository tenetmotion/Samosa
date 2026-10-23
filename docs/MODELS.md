# Model installation and first-use downloads

Samosa separates installing a checkpoint on disk from loading a model into memory.

## Installer modes

**Standard** installs SAM2 Base only. Existing Sammie packs are available on demand or through the model manager; missing packs download automatically when first requested. SAM 3.1 is not installed.

**Complete** pre-downloads every model in the pinned Sammie-Roto-2 registry. On Windows NVIDIA, it can also prepare isolated SAM 3.1 source/runtime after explicit Meta SAM License and gated-access acknowledgment. The hidden installer always leaves gated SAM 3.1 weights in `weights-pending`; finish the download from the panel or interactive manager. CorridorKey is installed separately on demand from the Matting tab.

**Custom** pre-downloads selected existing packs and can select SAM 3.1 on Windows NVIDIA. SAM 3.1 is managed outside Sammie's registry.

## Existing packs

| Pack | Feature | Terms |
| --- | --- | --- |
| SAM2 Base | Object selection/tracking | Apache-2.0 code; review model terms |
| SAM2 Large | Higher-capacity object selection/tracking | Apache-2.0 code; review model terms |
| EfficientTAM | Alternate object selection/tracking | Apache-2.0 code; review model terms |
| MatAnyone / MatAnyone2 | Matting | S-Lab noncommercial license |
| VideoMaMa | Matting | CC BY-NC 4.0 code; separate Stability AI model terms |
| MiniMax Remover | Removal | Additional noncommercial/model-host terms |
| CorridorKey 2.0 | Green/blue-screen video matting and linear cutout | Separate Corridor Key Licence; written permission required for commercial use |

## CorridorKey 2.0 metadata

CorridorKey runs in `runtime\CorridorKey2.0` so its pinned Python 3.12, PyTorch 2.9.1, torchvision 0.24.1, and CUDA 12.8 wheel dependencies do not alter Sammie's environment. Samosa invokes the unmodified upstream CLI through a process boundary. It is available only on Windows NVIDIA backends in this release; macOS, CPU, and Intel XPU report unsupported.

| Item | Pinned value |
| --- | --- |
| Official source | `nikopueringer/CorridorKey2.0` |
| Source commit | `485f5e9ab759ead6138cc990776184b59d6ea971` |
| Source archive SHA-256 | `9DB1ABA4597C739B5C4603D463EC2760984B3A3B74D2B320D1E7D07F2E329B07` |
| Hugging Face model | `nikopueringer/CorridorKey2.0` |
| Model revision | `cf44a8db3f024bb49fd59daa408848ccf05f7477` |
| Main checkpoint size | `2661698720` bytes |
| Main checkpoint SHA-256 | `C43596DE98CC8866D577C8A9F9EA239ACDF3F7A130FF24E25DB4375A50590436` |
| Foundation statistics size | `18864` bytes |
| Foundation statistics SHA-256 | `924783CEDDD293FD470126332CE328166FAEA6C4C8D2F36402D5B95187166AF3` |

The on-demand setup also downloads CorridorKey's pinned RVM, MoGe-2, and C-RADIO foundation dependencies through the official upstream downloader. Samosa verifies the two CorridorKey-owned checkpoint files exactly and validates all required foundation assets before reporting the engine ready. No CorridorKey source or weights are embedded in Samosa releases.

Review the [Corridor Key Licence](https://github.com/nikopueringer/CorridorKey2.0/blob/master/LICENSE) before setup. The license prohibits paid inference and requires written agreement for commercial software integration. Samosa's GPL-3.0 license does not override those terms.

## SAM 3.1 metadata

SAM 3.1 uses a separate environment at `runtime\SAM3.1` because its `numpy<2` dependency conflicts with Sammie's current runtime. It is supported only on Windows NVIDIA CUDA (`cu130` or `cu126`). macOS, CPU, and Intel XPU explicitly report unsupported.

The isolated environment pins Python 3.12, PyTorch 2.10.0, and torchvision 0.25.0 on the CUDA 12.6 wheel index. The Windows inference environment also pins `einops 0.8.1`, `triton-windows 3.6.0.post26`, `pycocotools 2.0.10`, and `psutil 7.2.1`, which the official SAM 3.1 inference import path uses but does not declare as base dependencies. The Triton version is paired with PyTorch 2.10. Setup imports the production predictor and executes a small CUDA distance-transform kernel before writing the environment marker.

| Item | Pinned value |
| --- | --- |
| Official source | `facebookresearch/sam3` |
| Source commit | `660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7` |
| Source archive SHA-256 | `CF25FAAAA0B83B8F5F7327B63E5DAA7E27EDED52EDA12BB4ADC01C9CF3D16090` |
| Hugging Face model | `facebook/sam3.1` |
| Model revision | `daa63191845a41281374e725f4c9e51c7a824460` |
| Checkpoint | `sam3.1_multiplex.pt` |
| Expected size | `3502755717` bytes |
| Expected SHA-256 | `0567DEBEEC80BA4AC6369540C6C248025283CB3FF2B92827509E57E2B3541CB6` |

The model is gated. Accept access at the [Hugging Face model page](https://huggingface.co/facebook/sam3.1) before downloading. The interactive manager reads `HF_TOKEN` from the current process or accepts a hidden prompt, then removes it after the download. It does not write credentials to state, configuration, logs, or arguments.

Source and environment markers make setup idempotent. Valid source and verified weights are skipped. Failed or mismatched checkpoints are removed. Downloader failures are emitted as structured, token-redacted messages so the panel can distinguish access, compatibility, and integrity errors. State is recorded in `runtime\SAM3.1\sam31-state.json` without secrets.

## Upstream model audit

The v1.4.0 release audit was completed on 2026-10-23 against official project sources and the pinned Sammie-Roto-2 v2.4.0 registry.

| Family | Result |
| --- | --- |
| Segment Anything | SAM 3.1 is the newer official generation and is added through the isolated integration described above. |
| EfficientTAM | The existing EfficientTAM family remains current for Sammie's adapter; no EfficientTAM2 release was substituted. |
| MatAnyone | MatAnyone2 is already the newer supported family and remains selectable alongside MatAnyone. |
| VideoMaMa | The official integration still exposes its original checkpoint set; no compatible successor checkpoint was published for this adapter. |
| MiniMax Remover | The official integration still exposes its original transformer and VAE checkpoint set. |
| Stable Video Diffusion VAE | A newer standalone SVD release is not a drop-in replacement for the VAE revision expected by VideoMaMa, so it was not swapped blindly. |
| CorridorKey | CorridorKey 2.0 is integrated as an isolated upstream engine; its weights and dependencies are not substituted or redistributed. |

Sammie-Roto-2 v2.4.0 did not introduce a new model-registry key that required another Samosa control. Future releases should repeat this audit rather than changing checkpoint URLs independently of the upstream adapters.

## Existing pack download behavior

Existing packs download to `.part`, verify their registry checksum, then atomically activate. Partial files are removed after a failed transfer or checksum check.
