# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

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
