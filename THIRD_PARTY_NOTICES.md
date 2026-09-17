# Third-party notices

Samosa's repository is GPL-3.0, but the complete runtime is assembled by the user from separately licensed projects. This is a release checklist, not legal advice. Review current upstream terms before use or redistribution.

| Component | Role | License or terms | Notes |
| --- | --- | --- | --- |
| Sammie-Roto-2 | Runtime and orchestration | GPL-3.0 | v1.3.0 pins source commit `6f2213dfd8bf15c54ef41b9c9eff7af2cfee1c06` (v2.4.0). Preserve source and notices when distributing it. |
| SAM 2 | Segmentation | Apache-2.0 code; model terms may also apply | Downloaded through Sammie's registry. |
| EfficientTAM | Segmentation | Apache-2.0 code; model terms may also apply | Downloaded through Sammie's registry. |
| MatAnyone / MatAnyone2 | Video matting | S-Lab License 1.0 | Noncommercial unless separate permission is obtained. |
| VideoMaMa code | Video matting | CC BY-NC 4.0 | Noncommercial terms apply; model dependencies have separate terms. |
| Stable Video Diffusion VAE | VideoMaMa dependency | Stability AI Community License | Separate terms apply. |
| MiniMax Remover | Object removal | Additional noncommercial/model-host terms | Review current upstream and host terms. |
| OpenCV | Image processing and removal | Apache-2.0 | Follow installed package notices. |
| SAM 3.1 source | Optional segmentation | Meta SAM License | Official source commit `660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7`. This is not Apache-2.0 or GPL-3.0. |
| SAM 3.1 checkpoint | Optional segmentation weights | Gated Hugging Face access plus Meta SAM License | `facebook/sam3.1`, revision `daa63191845a41281374e725f4c9e51c7a824460`, checkpoint `sam3.1_multiplex.pt`. |
| einops | SAM 3.1 tensor operations | MIT | Windows SAM 3.1 runtime pins `0.8.1`. |
| triton-windows | SAM 3.1 CUDA kernels on Windows | MIT | Windows SAM 3.1 runtime pins `3.6.0.post26` for PyTorch 2.10. |
| pycocotools | SAM 3.1 shared data structures | FreeBSD | Windows SAM 3.1 runtime pins `2.0.10`. |
| psutil | SAM 3.1 worker process support | BSD-3-Clause | Windows SAM 3.1 runtime pins `7.2.1`. |
| Adobe CEP / After Effects SDK | Host integration | Adobe terms | Not included in Samosa releases. Obtain SDK materials from Adobe. |

Samosa releases do not embed third-party model checkpoints or Hugging Face tokens. The Sammie-Roto-2 source archive is pinned to SHA-256 `829670BBDE1A92445DCF3D6716D94E35729974799B058C5169E17125A906BE59`. The Windows installer downloads SAM 3.1 source only when the user selects it in Complete or Custom mode, after explicit acknowledgment. It creates an isolated environment and always records `weights-pending`; the hidden installer never requests or consumes a Hugging Face token. Gated weights are downloaded later through the interactive manager after the user accepts access at Hugging Face. The manager verifies the pinned revision, expected size, and SHA-256.

SAM 3.1 is not supported on macOS, CPU, or Intel XPU in this release. The macOS scripts report that boundary and do not install it.

Do not infer commercial-use permission from installation. Do not redistribute Meta SAM source or weights without complying with the applicable Meta SAM License, gated-access terms, notices, and other upstream conditions. Samosa and this notice do not provide legal advice or a commercial-use grant.

Upstream locations:

- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/sam2/LICENSE
- https://github.com/yformer/EfficientTAM/blob/main/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/matanyone/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/videomama/License.md
- https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt/blob/main/LICENSE.md
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/minimax_remover/LICENSE
- https://github.com/facebookresearch/sam3/blob/660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7/LICENSE
- https://huggingface.co/facebook/sam3.1
- https://github.com/arogozhnikov/einops
- https://github.com/woct0rdho/triton-windows
- https://github.com/ppwwyyxx/cocoapi
- https://github.com/giampaolo/psutil
