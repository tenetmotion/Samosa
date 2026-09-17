#!/usr/bin/env python3
"""Download the gated SAM 3.1 checkpoint without persisting an access token."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


MODEL_REPO = "facebook/sam3.1"
MODEL_REVISION = "daa63191845a41281374e725f4c9e51c7a824460"
CHECKPOINT = "sam3.1_multiplex.pt"
EXPECTED_BYTES = 3502755717
EXPECTED_SHA256 = "0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6"


def emit(event: str, **values: object) -> None:
    print("SAMOSA_SAM31 " + json.dumps({"event": event, **values}), flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_error(error: Exception) -> str:
    message = str(error).strip() or type(error).__name__
    token = os.environ.get("HF_TOKEN")
    if token:
        message = message.replace(token, "[redacted]")
    message = re.sub(r"hf_[A-Za-z0-9_-]+", "[redacted]", message)
    return " ".join(message.split())[:1200]


def classify_error(error: Exception) -> tuple[str, str]:
    message = safe_error(error)
    lowered = message.lower()
    if "403 client error" in lowered and (
        "gated repo" in lowered
        or "not in the authorized list" in lowered
        or "access to model facebook/sam3.1 is restricted" in lowered
    ):
        return (
            "Hugging Face has not granted this token's account access to SAM 3.1. "
            "Open the access page while signed into the same account, submit the "
            "Meta access form, wait until access is granted, then retry with a "
            "read token from that account.",
            "gated_access_denied",
        )
    if "401 client error" in lowered or "invalid user token" in lowered:
        return (
            "Hugging Face rejected this token. Create a valid read token from the "
            "same account that has SAM 3.1 access, then retry.",
            "invalid_token",
        )
    return message, "download_failed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        emit("pending", reason="HF_TOKEN is not available in this process")
        return 2

    destination = args.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == EXPECTED_BYTES and sha256(destination) == EXPECTED_SHA256:
        emit("skipped", file=destination.name, bytes=EXPECTED_BYTES)
        return 0
    if destination.exists():
        destination.unlink()

    try:
        from huggingface_hub import hf_hub_download

        emit("started", file=CHECKPOINT, revision=MODEL_REVISION)
        cached = Path(
            hf_hub_download(
                repo_id=MODEL_REPO,
                filename=CHECKPOINT,
                revision=MODEL_REVISION,
                token=token,
                local_dir=str(destination.parent),
            )
        )
        if cached != destination:
            os.replace(cached, destination)
        actual_bytes = destination.stat().st_size
        actual_digest = sha256(destination)
        if actual_bytes != EXPECTED_BYTES or actual_digest != EXPECTED_SHA256:
            destination.unlink(missing_ok=True)
            raise RuntimeError(
                f"SAM 3.1 checkpoint integrity mismatch: expected {EXPECTED_BYTES} bytes and {EXPECTED_SHA256}, got {actual_bytes} bytes and {actual_digest}"
            )
    except Exception as error:
        message, error_code = classify_error(error)
        emit(
            "error",
            message=message,
            error_type=type(error).__name__,
            error_code=error_code,
        )
        return 3
    emit("complete", file=destination.name, bytes=actual_bytes, sha256=actual_digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
