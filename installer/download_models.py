#!/usr/bin/env python3
"""Prefetch model checkpoints from Sammie-Roto-2's own registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def digest(path: Path) -> str:
    checksum = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def load_registry(repo: Path):
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from sammie.model_downloader import MODEL_REGISTRY

    return MODEL_REGISTRY


def resolve_keys(raw: str, registry) -> list[str]:
    if raw.strip().lower() == "all":
        return list(registry)
    keys = []
    for key in raw.split(","):
        key = key.strip()
        if key and key not in keys:
            if key not in registry:
                raise ValueError("Unknown model key %r; available: %s" % (key, ", ".join(registry)))
            keys.append(key)
    return keys


def emit(event: str, **values) -> None:
    print("SAMOSA_MODEL " + json.dumps({"event": event, **values}), flush=True)


def download(key: str, spec, requests) -> None:
    spec.final_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.final_path.exists():
        if digest(spec.final_path) == spec.md5:
            emit("skipped", key=key, file=spec.filename)
            return
        spec.final_path.unlink()
    if spec.part_path.exists():
        spec.part_path.unlink()

    emit("started", key=key, file=spec.filename)
    response = requests.get(spec.url, stream=True, timeout=30, headers={"Accept-Encoding": "identity"})
    response.raise_for_status()
    total = int(response.headers.get("content-length", "0"))
    downloaded = 0
    last_percent = -1
    try:
        with spec.part_path.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                percent = int(downloaded * 100 / total) if total else 0
                if percent >= last_percent + 5 or downloaded == total:
                    emit("progress", key=key, file=spec.filename, percent=percent, bytes=downloaded, total=total)
                    last_percent = percent
        actual = digest(spec.part_path)
        if actual != spec.md5:
            raise RuntimeError("Checksum mismatch for %s: expected %s, got %s" % (spec.filename, spec.md5, actual))
        os.replace(spec.part_path, spec.final_path)
        emit("complete", key=key, file=spec.filename)
    except Exception:
        spec.part_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--models", default="Base")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    repo = args.repo.resolve()
    registry = load_registry(repo)
    if args.list:
        print(json.dumps({key: {"file": spec.filename, "md5": spec.md5} for key, spec in registry.items()}, indent=2))
        return 0

    keys = resolve_keys(args.models, registry)
    import requests

    for key in keys:
        download(key, registry[key], requests)
    emit("all_complete", models=keys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
