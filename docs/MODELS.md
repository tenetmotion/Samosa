# Model installation and first-use downloads

Samosa separates installing a checkpoint on disk from loading a model into memory.

## Installer modes

**Standard** installs SAM2 Base only. Existing Sammie packs are available on demand or through the model manager; missing packs download automatically when first requested. SAM 3.1 is not installed.

**Complete** pre-downloads every model in the pinned Sammie-Roto-2 registry. On Windows NVIDIA, it can also prepare isolated SAM 3.1 source/runtime after explicit Meta SAM License and gated-access acknowledgment. The hidden installer always leaves gated SAM 3.1 weights in `weights-pending`; finish the download from the panel or interactive manager.

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

The v1.3.0 release audit was completed on 2026-09-04 against official project sources and the pinned Sammie-Roto-2 v2.4.0 registry.

| Family | Result |
| --- | --- |
| Segment Anything | SAM 3.1 is the newer official generation and is added through the isolated integration described above. |
| EfficientTAM | The existing EfficientTAM family remains current for Sammie's adapter; no EfficientTAM2 release was substituted. |
| MatAnyone | MatAnyone2 is already the newer supported family and remains selectable alongside MatAnyone. |
| VideoMaMa | The official integration still exposes its original checkpoint set; no compatible successor checkpoint was published for this adapter. |
| MiniMax Remover | The official integration still exposes its original transformer and VAE checkpoint set. |
| Stable Video Diffusion VAE | A newer standalone SVD release is not a drop-in replacement for the VAE revision expected by VideoMaMa, so it was not swapped blindly. |

Sammie-Roto-2 v2.4.0 did not introduce a new model-registry key that required another Samosa control. Future releases should repeat this audit rather than changing checkpoint URLs independently of the upstream adapters.

## Existing pack download behavior

Existing packs download to `.part`, verify their registry checksum, then atomically activate. Partial files are removed after a failed transfer or checksum check.
