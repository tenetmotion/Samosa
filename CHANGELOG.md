# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

## [1.4.0] - 2026-10-23

- Fixed Windows SAM 3.1 setup by updating the Hugging Face downloader API, installing the complete minimal inference dependency set, validating the Triton CUDA kernel before marking the environment ready, and preserving actionable setup errors instead of wrapped command fragments.
- Added explicit handling for gated-repository `403` responses so the panel explains account approval and token-account matching instead of displaying the raw request ID.
- Fixed SAM 3.1 cold starts by separating worker readiness from model loading, running CUDA initialization on the worker main thread, and reporting startup phases instead of terminating a healthy worker after 30 seconds.
- Fixed SAM 3.1 video sessions by passing absolute cross-process frame and mask paths, adapting the pinned upstream session signature, and treating empty detections as valid results.
- Added optional CorridorKey 2.0 matting on Windows NVIDIA with tracked-mask, first-frame, and automatic key modes.
- Added pinned, checksum-verified, license-gated CorridorKey source/model setup in an isolated Python and CUDA runtime.
- Added frame-aligned alpha ingestion plus scene-linear EXR cutout preservation, sRGB PNG conversion, and alpha-capable video exports.

## [1.3.0] - 2026-09-04

- Added optional SAM 3.1 selection and tracking on Windows NVIDIA with point, box, text, and positive/negative example prompts.
- Isolated SAM 3.1 in its own Python environment and worker process to preserve Sammie-Roto-2 dependency compatibility and After Effects responsiveness.
- Added gated, checksum-verified SAM 3.1 setup with explicit Meta SAM License acknowledgment and memory-only Hugging Face token handling.
- Updated the pinned Sammie-Roto-2 runtime from 2.3.3 to 2.4.0 after running the existing integration suite against the new revision.
- Added SAM 3.1 protocol, lifecycle, mask-transport, installer, service, and panel regression coverage.
- Documented the current model-family audit; no unsupported checkpoint substitutions were made for EfficientTAM, MatAnyone2, VideoMaMa, MiniMax Remover, or SVD VAE.

## [1.2.1] - 2026-08-13

- Fixed Windows PowerShell 5.1 setup by decoding the downloaded uv installer explicitly as UTF-8.
- Limited decoding to the selected After Effects layer's source-time In/Out interval.
- Fixed source-frame selection and imported-result placement for trimmed and time-stretched footage.
- Preserved interpreted footage frame rates, added VFR timestamp sampling, and conformed generated sequences correctly.
- Added backend identity checks and per-panel lifecycle registration so outdated detached services do not remain active and shared services are not stopped prematurely.
- Added synthetic trim, HTTP range, lifecycle, installer, and panel timing regression tests.

## [1.2.0] - 2026-07-11

- Added an unsigned macOS PKG for Apple Silicon and Intel Macs.
- Added per-user macOS runtime installation, MPS/CPU selection, model management, and uninstall launchers.
- Made CEP service logs, startup messages, and output reveal behavior platform-aware.
- Added macOS package and pinned-runtime validation in GitHub Actions.

## [1.1.0] - 2026-07-11

- Added a per-user Windows EXE that downloads a pinned Sammie-Roto-2 runtime into one Samosa installation root.
- Added Standard, Complete, and Custom model installation modes with explicit noncommercial license acceptance.
- Added checksum-verified model prefetching, on-demand download status, and a post-install model manager.

## [1.0.0] - 2026-07-10

- Initial Samosa open-source release.
- Added docked object selection, tracking, matting, removal, output, and import workflow.
- Added local headless bridge to a separately installed Sammie-Roto-2 runtime.
- Added background job progress, cancellation, tracking status, and viewer scaling.
- Applied the Samosa editor-workspace redesign with the supplied wordmark, compact black/orange controls, responsive dock layouts, and offline-safe visual assets.
- Added a persistent native folder selector for choosing the export destination directly from the Output tab.
- Fixed progress status, percentage, and cancellation controls so they remain legible without overlap at every supported dock width.
- Changed export defaults to use the source clip name and automatically append the selected object/all and output-description tags.
