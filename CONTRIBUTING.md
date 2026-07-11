# Contributing to Samosa

## Before opening a change

1. Search existing issues and keep each pull request focused.
2. Do not commit model weights, checkpoints, Adobe SDK files, generated `config.json`, media, logs, or user data.
3. Preserve the headless service boundary; upstream model logic belongs in Sammie-Roto-2 whenever practical.
4. Keep ExtendScript compatible with ES3: use `var`, avoid arrow functions and modern array helpers, and wrap project mutations in one undo group.
5. Add or update tests for user-visible behavior and API contract changes.

## Test

```powershell
python -m unittest discover -s tests -v
```

Integration tests require `SAMMIE_REPO` and the Python interpreter from that checkout. Pull requests should describe the After Effects version and GPU used for any manual test.

## Licensing

By submitting a contribution, you agree that it is licensed under GPL-3.0. Do not contribute code or assets that cannot be distributed under those terms. Model adapters must document the model's separate license in `THIRD_PARTY_NOTICES.md`.
