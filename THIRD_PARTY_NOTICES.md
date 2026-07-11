# Third-party notices

Samosa's repository is GPL-3.0, but the complete runtime is assembled by the user from separately licensed projects. This file is a release checklist, not legal advice. Review the current upstream terms before use or redistribution.

| Component | Role | License observed in Sammie-Roto-2 | Commercial-use note |
| --- | --- | --- | --- |
| Sammie-Roto-2 | Runtime and orchestration | GPL-3.0 | Source and notices must remain available when distributed. |
| SAM 2 | Segmentation | Apache-2.0 | Preserve Apache license and notices. Model terms must also be checked. |
| EfficientTAM | Segmentation | Apache-2.0 | Preserve Apache license and notices. Model terms must also be checked. |
| MatAnyone / MatAnyone2 | Video matting | S-Lab License 1.0 | Source and binary use is limited to noncommercial purposes unless permission is obtained. |
| VideoMaMa | Video matting | CC BY-NC 4.0 | Noncommercial only under the bundled VideoMaMa license. |
| Stable Video Diffusion VAE | VideoMaMa dependency | Stability AI Community License | Separate terms apply, including commercial-use conditions and revenue thresholds. |
| MiniMax Remover | Object removal | Noncommercial terms | The pinned Sammie-Roto-2 source includes CC BY-NC 4.0; review the current model-host terms before use. |
| OpenCV | Image processing and removal | Apache-2.0 | Follow the installed package's notices. |
| Adobe CEP / After Effects SDK | Host integration | Adobe terms | Not included in Samosa releases. Obtain SDK materials from Adobe. |

Samosa release packages do not embed model checkpoints or weights. The online installer and first-use model flow download selected files directly from the URLs in Sammie-Roto-2's model registry after any required restriction notice. Redistribution of an offline bundle still requires separate verification and compliance with every applicable code and model license.

Upstream license locations:

- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/sam2/LICENSE
- https://github.com/yformer/EfficientTAM/blob/main/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/matanyone/LICENSE
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/videomama/License.md
- https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt/blob/main/LICENSE.md
- https://github.com/Zarxrax/Sammie-Roto-2/blob/main/minimax_remover/LICENSE
