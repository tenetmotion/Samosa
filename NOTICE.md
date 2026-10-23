# Samosa notices

Samosa is an independent After Effects integration built to interoperate with Sammie-Roto-2.

## Sammie-Roto-2

- Project: Sammie-Roto-2
- Author/maintainer: Zarxrax and contributors
- Source: https://github.com/Zarxrax/Sammie-Roto-2
- License: GNU General Public License version 3
- Pinned release: v2.4.0
- Pinned commit: `6f2213dfd8bf15c54ef41b9c9eff7af2cfee1c06`
- Archive SHA-256: `829670BBDE1A92445DCF3D6716D94E35729974799B058C5169E17125A906BE59`

Samosa release packages do not embed the Sammie-Roto-2 source tree or model weights. Online installers download the pinned revision and selected model files from upstream, verify them, and assemble a local runtime. The bridge imports Sammie-Roto-2 modules at runtime and adapts its desktop processing pipeline for a headless local service.

## Optional SAM 3.1 integration

Samosa can prepare the official SAM 3.1 source in a separate Windows NVIDIA runtime:

- Source: https://github.com/facebookresearch/sam3
- Source commit: `660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7`
- Source archive SHA-256: `CF25FAAAA0B83B8F5F7327B63E5DAA7E27EDED52EDA12BB4ADC01C9CF3D16090`
- Model: https://huggingface.co/facebook/sam3.1
- Model revision: `daa63191845a41281374e725f4c9e51c7a824460`
- Checkpoint: `sam3.1_multiplex.pt`
- Expected size: `3502755717` bytes
- Expected SHA-256: `0567DEBEEC80BA4AC6369540C6C248025283CB3FF2B92827509E57E2B3541CB6`

SAM 3.1 source and weights are governed by the Meta SAM License and gated Hugging Face access. These terms are distinct from Sammie-Roto-2's GPL-3.0 license and are not Apache-2.0 or GPL-3.0. Samosa does not bundle or redistribute the SAM 3.1 source or checkpoint. A user who selects SAM 3.1 in Windows Complete or Custom mode must review the terms and obtain gated access. The hidden installer never requests a token; the interactive manager may read `HF_TOKEN` or hidden stdin input for the current process only. Credentials are not stored in state or logs.

SAM 3.1 is unsupported on macOS, CPU, and Intel XPU in this release. The macOS installer reports that limitation and preserves existing SAM2/EfficientTAM workflows.

## Optional CorridorKey 2.0 integration

Samosa can download and invoke the unmodified CorridorKey 2.0 CLI in a separate Windows NVIDIA runtime:

- Project: CorridorKey 2.0 by Niko Pueringer and Corridor Digital
- Source: https://github.com/nikopueringer/CorridorKey2.0
- Source commit: `485f5e9ab759ead6138cc990776184b59d6ea971`
- Source archive SHA-256: `9DB1ABA4597C739B5C4603D463EC2760984B3A3B74D2B320D1E7D07F2E329B07`
- Model: https://huggingface.co/nikopueringer/CorridorKey2.0
- Model revision: `cf44a8db3f024bb49fd59daa408848ccf05f7477`

Samosa does not embed, modify, or redistribute CorridorKey source or weights. Users explicitly accept the separate Corridor Key Licence before the on-demand manager downloads upstream assets. That license prohibits paid inference and requires a written agreement for commercial software integration. Samosa's GPL-3.0 license does not grant additional CorridorKey rights.

CorridorKey 2.0 is unsupported on macOS, CPU, and Intel XPU in this release.

The name Samosa identifies this integration project and must not imply endorsement by the Sammie-Roto-2, SAM 3.1, or CorridorKey maintainers.

## Adobe

Adobe, After Effects, and Premiere Pro are trademarks of Adobe. Samosa is not affiliated with, sponsored by, or endorsed by Adobe. Adobe SDK files are not distributed with this project and remain subject to Adobe's terms.
