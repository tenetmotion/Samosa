# Model installation and first-use downloads

Samosa separates installing a model on disk from loading it into memory. Installed checkpoints remain under the Samosa runtime; only the model currently being used is loaded into RAM/VRAM.

## Installer modes

### Standard

Standard pre-downloads SAM2 Base, which supports the default Object workflow. Other model packs remain available without reinstalling Samosa.

Missing model packs download automatically when first requested.

When an uninstalled model is first requested:

1. The local Samosa service asks Sammie-Roto-2's model registry for that model's URL, expected checksum, and destination.
2. The checkpoint downloads to a `.part` file inside the Samosa runtime.
3. The download is checksum-verified before activation.
4. The `.part` file is atomically renamed to its final checkpoint name.
5. The selected model loads into memory and the requested job continues.

The checkpoint remains on disk, so subsequent uses load locally without another download. Background matting/removal jobs show the active download filename and progress in the Samosa job bar. The first SAM2 model download occurs while the first Include point reports that the segmentation model is loading.

### Complete

Complete runs the same checksum-verified downloader during installation for every model in the pinned Sammie-Roto-2 registry. Models are installed on disk but are still loaded into memory only when used.

### Custom

Custom pre-downloads selected packs. Any unselected pack retains the same Standard on-demand behavior.

## Available packs

| Pack | Samosa feature | License note |
| --- | --- | --- |
| SAM2 Base | Default object selection/tracking | Apache-2.0 code; review model terms |
| SAM2 Large | Higher-capacity object selection/tracking | Apache-2.0 code; review model terms |
| EfficientTAM | Alternate object selection/tracking | Apache-2.0 code; review model terms |
| MatAnyone | Matting | S-Lab noncommercial license |
| MatAnyone2 | Matting | S-Lab noncommercial license |
| VideoMaMa | Matting | VideoMaMa is CC BY-NC 4.0; its SVD VAE dependency has separate Stability AI Community License terms |
| MiniMax Remover | Object removal | Noncommercial terms; pinned source includes CC BY-NC 4.0, and current model-host terms must be reviewed |

See [Third-party notices](../THIRD_PARTY_NOTICES.md) for upstream links and detailed restrictions.

## Adding packs later

- Open **Start > Samosa > Manage model packs** to pre-download one or more packs.
- Rerun the Samosa EXE and choose Complete or Custom. Existing files with valid checksums are skipped.
- Simply select the model in Samosa and run its tool to use the on-demand path.

Model downloads require internet access. Partial files retain a `.part` extension and are removed after a failed checksum or transfer. Logs are stored under `%LOCALAPPDATA%\Programs\Samosa\logs` on Windows and `~/Library/Application Support/Samosa/logs` on macOS.
