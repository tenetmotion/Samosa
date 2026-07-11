# Releasing

## Version preparation

1. Update versions in `panel/CSXS/manifest.xml`, `backend/service.py`, `CHANGELOG.md`, and `CITATION.cff`.
2. Verify the current Sammie-Roto-2 revision and update `NOTICE.md`.
3. Recheck every upstream code and model license in `THIRD_PARTY_NOTICES.md`.
4. Run contract and integration tests.
5. Complete the manual After Effects workflow in `DEVELOPMENT.md`.
6. Confirm no generated configuration, private media, logs, weights, SDK files, or compiled Adobe plug-ins are tracked.

## Build the source archive

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-release.ps1 -Version 1.0.0
```

The script validates the tree and writes a source directory and zip under `dist/`. Inspect both before publishing. GitHub source archives remain the canonical corresponding source for GPL releases.

## Publish

Create a signed tag named `vX.Y.Z`, push it, and create a GitHub release containing the changelog entry and generated source zip. State explicitly that Sammie-Roto-2 and model weights are separate downloads and repeat the noncommercial-engine warning.

Do not upload Adobe SDK materials, checkpoint files, a configured `config.json`, or a bundled third-party Python environment.
