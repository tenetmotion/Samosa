#!/bin/bash
set -euo pipefail
echo "SAM 3.1 is unsupported on macOS. It requires a Windows NVIDIA CUDA runtime; use SAM2 or EfficientTAM on this platform." >&2
exit 2
