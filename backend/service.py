#!/usr/bin/env python3
"""Headless Samosa bridge to a local Sammie-Roto-2 checkout."""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict
from fractions import Fraction
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
DEFAULT_PORT = 43831
VERSION = "1.3.0"
_progress_context = threading.local()
OUTPUT_TAGS = {
    "Segmentation-Alpha": "segmentation_alpha",
    "Segmentation-Matte": "segmentation_matte",
    "Segmentation-BGcolor": "segmentation_bgcolor",
    "Matting-Alpha": "matting_alpha",
    "Matting-Matte": "matting_matte",
    "Matting-BGcolor": "matting_bgcolor",
    "ObjectRemoval": "object_removal",
}

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from sam3_bridge import Sam3Bridge, Sam3BridgeError


def _backend_sha256():
    digest = hashlib.sha256()
    for name in ("service.py", "sam3_bridge.py", "sam3_worker.py"):
        path = BACKEND_DIR / name
        if path.is_file():
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


BACKEND_SHA256 = _backend_sha256()


def _finite_seconds(value, name):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ApiError("%s must be a finite number" % name)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ApiError("%s must be a finite number" % name)
    if not math.isfinite(number):
        raise ApiError("%s must be a finite number" % name)
    if number < 0:
        raise ApiError("%s must be non-negative" % name)
    return number


def _fraction(value):
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def _snap_near_integer(product):
    """Remove float serialization noise at exact frame boundaries."""
    nearest = int(product + Fraction(1, 2))
    if abs(product - nearest) <= Fraction(1, 1_000_000_000):
        return Fraction(nearest)
    return product


def _calculate_frame_bounds(source_start, source_end, fps):
    """Return inclusive/exclusive source frame bounds for a time range."""
    start = _finite_seconds(source_start, "source_start")
    end = _finite_seconds(source_end, "source_end")
    if start is None:
        start = 0.0
    if end is not None and end <= start:
        raise ApiError("source_end must be greater than source_start")
    fps_fraction = _fraction(fps)
    if fps_fraction <= 0:
        raise ApiError("Video frame rate must be positive")
    start_product = _snap_near_integer(_fraction(start) * fps_fraction)
    start_frame = start_product.numerator // start_product.denominator
    end_frame = None
    if end is not None:
        end_product = _snap_near_integer(_fraction(end) * fps_fraction)
        end_frame = -((-end_product.numerator) // end_product.denominator)
        if end_frame <= start_frame:
            raise ApiError("The requested range does not contain a video frame")
    return start, end, start_frame, end_frame


def _parent_process_exists(parent_pid):
    """Check process existence without requiring psutil on Windows or macOS."""
    try:
        parent_pid = int(parent_pid)
    except (TypeError, ValueError):
        return False
    if parent_pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, parent_pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.windll.kernel32.GetLastError() == 5
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ClientRegistry:
    """Tracks CEP panel clients so one panel cannot stop another panel's service."""

    def __init__(self):
        self.lock = threading.RLock()
        self.clients = {}
        self.managed = False
        self.empty_since = None

    def register(self, token, parent_pid):
        token = str(token or "").strip()
        if not token or len(token) > 128:
            raise ApiError("client_token must contain 1 to 128 characters")
        try:
            parent_pid = int(parent_pid)
        except (TypeError, ValueError):
            raise ApiError("parent_pid must be a process id")
        if not _parent_process_exists(parent_pid):
            raise ApiError("The panel process is not running")
        with self.lock:
            # CEP reloads can replace the document without delivering a usable
            # beforeunload request. A new token from the same renderer process
            # supersedes that stale registration.
            self.clients = {
                existing_token: existing_pid
                for existing_token, existing_pid in self.clients.items()
                if existing_pid != parent_pid or existing_token == token
            }
            self.clients[token] = parent_pid
            self.managed = True
            self.empty_since = None
            return len(self.clients)

    def release(self, token):
        with self.lock:
            self.clients.pop(str(token or ""), None)
            if self.managed and not self.clients and self.empty_since is None:
                self.empty_since = time.monotonic()
            return len(self.clients)

    def count(self):
        with self.lock:
            return len(self.clients)

    def unused_for(self, grace_period=2.0):
        with self.lock:
            alive = {}
            for token, parent_pid in self.clients.items():
                if _parent_process_exists(parent_pid):
                    alive[token] = parent_pid
            self.clients = alive
            if self.clients:
                self.empty_since = None
                return False
            if not self.managed:
                return False
            if self.empty_since is None:
                self.empty_since = time.monotonic()
                return False
            return time.monotonic() - self.empty_since >= grace_period


def start_client_watchdog(server, registry, poll_interval=0.5, grace_period=2.0):
    """Stop the service after every registered CEP panel process has exited."""
    stop_event = threading.Event()

    def watch():
        while not stop_event.wait(poll_interval):
            if registry.unused_for(grace_period):
                server.shutdown()
                return

    thread = threading.Thread(target=watch, name="samosa-client-watchdog", daemon=True)
    thread.start()
    return stop_event, thread


def shutdown_if_unused(server, registry, delay=2.0):
    """Allow an unloading/reloading panel a short window to register again."""
    time.sleep(delay)
    if registry.unused_for(delay):
        server.shutdown()


class NullProgressDialog:
    """Qt progress replacement used by legacy Sammie processing code."""

    cancelled = False

    def __init__(self, *args, **kwargs):
        self.value = 0
        self.minimum = int(args[2]) if len(args) > 2 else 0
        self.maximum = int(args[3]) if len(args) > 3 else 100

    def setWindowTitle(self, *args): pass
    def setWindowModality(self, *args): pass
    def setAutoClose(self, *args): pass
    def setLabelText(self, message):
        job = getattr(_progress_context, "job", None)
        if job and message:
            job.message = str(message)
    def show(self): pass
    def close(self): pass
    def setMaximum(self, maximum): self.maximum = int(maximum)
    def setRange(self, minimum, maximum):
        self.minimum = int(minimum)
        self.maximum = int(maximum)
    def setValue(self, value):
        self.value = value
        job = getattr(_progress_context, "job", None)
        if job and self.maximum > self.minimum:
            progress = (float(value) - self.minimum) * 100.0 / (self.maximum - self.minimum)
            job.progress = max(0, min(100, int(round(progress))))
    def wasCanceled(self):
        job = getattr(_progress_context, "job", None)
        return self.cancelled or bool(job and job.cancelled)


class NullApplication:
    @staticmethod
    def processEvents():
        return None


class FrameSlider:
    def setValue(self, value):
        return None


class ParentShim:
    def __init__(self, settings_mgr):
        self.settings_mgr = settings_mgr
        self.frame_slider = FrameSlider()


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = "queued"
        self.progress = 0
        self.message = "Queued"
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.cancelled = False
        self.cancel_event = threading.Event()
        self.process = None

    def payload(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
        }


class Engine:
    SETTING_KEYS = {
        "sam_model", "holes", "dots", "border_fix", "grow", "show_masks",
        "show_outlines", "antialias", "show_all_points", "bgcolor",
        "matany_model", "matany_res", "matany_overlap", "matany_chunk",
        "matany_combined", "matany_grow", "matany_gamma", "inpaint_method",
        "inpaint_radius", "inpaint_grow", "minimax_steps",
        "minimax_resolution", "minimax_vae_tiling", "in_point", "out_point",
        "object_names", "selected_object_id",
    }

    def __init__(
        self,
        repo: Path,
        mock: bool = False,
        sam3_python=None,
        sam3_repo=None,
        sam3_checkpoint=None,
        install_root=None,
        backend=None,
    ):
        self.repo = repo.resolve()
        self.mock = mock
        self.lock = threading.RLock()
        self.jobs = {}
        self.loaded_path = None
        self.source_start_time = 0.0
        self.source_end_time = None
        self.source_start_frame = 0
        self.source_end_frame_exclusive = 0
        self.trimmed = False
        self.points = []
        self.sam_manager = None
        self.sam3_bridge = None
        self.sam3_session_id = None
        self.sam3_prompts = []
        self.sam3_object_ids = set()
        self.sam3_install_job_id = None
        self.matting_manager = None
        self.removal_manager = None
        inferred_root = (
            self.repo.parent.parent
            if self.repo.parent.name.lower() == "runtime"
            else Path(__file__).resolve().parents[1]
        )
        self.install_root = Path(
            install_root or os.environ.get("SAMOSA_INSTALL_ROOT") or inferred_root
        ).resolve()
        default_sam3_root = self.install_root / "runtime" / "SAM3.1"
        default_sam3_python = (
            default_sam3_root / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else default_sam3_root / ".venv" / "bin" / "python"
        )
        self.sam3_repo = Path(
            sam3_repo or os.environ.get("SAMOSA_SAM3_REPO") or default_sam3_root
        ).resolve()
        self.sam3_python = Path(
            sam3_python or os.environ.get("SAMOSA_SAM3_PYTHON") or default_sam3_python
        ).resolve()
        self.sam3_checkpoint = Path(
            sam3_checkpoint
            or os.environ.get("SAMOSA_SAM3_CHECKPOINT")
            or default_sam3_root / "models" / "sam3.1_multiplex.pt"
        ).resolve()
        self.backend = str(backend or os.environ.get("SAMOSA_BACKEND") or "").lower()
        self._load_modules()
        session_temp = Path(self.core.temp_dir).resolve()
        self.sam3_transport_dir = session_temp / "sam3_transport"
        self.sam3_frames_dir = session_temp / "sam3_frames"

    def _load_modules(self):
        if not self.repo.exists():
            raise RuntimeError("Sammie repo does not exist: %s" % self.repo)
        os.chdir(self.repo)
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        import cv2
        import numpy as np
        import av
        from sammie import core
        from sammie import sammie as sammie_api
        from sammie.settings_manager import get_settings_manager
        from sammie.matting import create_matting_manager
        from sammie.removal import RemovalManager

        self.cv2 = cv2
        self.np = np
        self.av = av
        self.core = core
        self.sammie_api = sammie_api
        self.settings_mgr = get_settings_manager()
        self.create_matting_manager = create_matting_manager
        self.RemovalManager = RemovalManager
        self._isolate_ae_session()
        self._patch_headless_ui()

    def _isolate_ae_session(self):
        """Keep the panel session separate from the standalone Sammie app."""
        session_root = os.environ.get("SAMOSA_SESSION_ROOT")
        if not session_root:
            session_root = os.path.join("temp", "ae_test_%d" % os.getpid()) if self.mock else os.path.join("temp", "ae_session")
        self.core.temp_dir = session_root
        self.core.frames_dir = os.path.join(session_root, "frames")
        self.core.mask_dir = os.path.join(session_root, "masks")
        self.core.backup_dir = os.path.join(session_root, "masks_backup")
        self.core.matting_dir = os.path.join(session_root, "matting")
        self.core.removal_dir = os.path.join(session_root, "removal")
        self.settings_mgr.temp_dir = session_root
        self.settings_mgr.session_settings_file = os.path.join(session_root, "session_settings.conf")
        self.settings_mgr.points_file = os.path.join(session_root, "points.json")
        import sammie.duplicate_frame_handler as dedupe_mod
        dedupe_mod.frames_dir = self.core.frames_dir
        dedupe_mod.mask_dir = self.core.mask_dir
        dedupe_mod.backup_dir = self.core.backup_dir

    def _patch_headless_ui(self):
        import sammie.sammie as sam_mod
        import sammie.matting as mat_mod
        import sammie.removal as removal_mod
        import sammie.duplicate_frame_handler as dedupe_mod
        for module in (sam_mod, mat_mod, removal_mod, dedupe_mod):
            if hasattr(module, "QProgressDialog"):
                module.QProgressDialog = NullProgressDialog
            if hasattr(module, "QApplication"):
                module.QApplication = NullApplication
        sam_mod.ensure_models = self._ensure_models_headless
        mat_mod.ensure_models = self._ensure_models_headless
        removal_mod.ensure_models = self._ensure_models_headless

    def _ensure_models_headless(self, models, parent=None, title=None):
        """Download missing checkpoints without constructing Qt windows."""
        import requests
        from sammie.model_downloader import MODEL_REGISTRY
        if models == "all":
            specs = list(MODEL_REGISTRY.values())
        else:
            if not isinstance(models, list):
                models = [models]
            specs = [MODEL_REGISTRY[item] if isinstance(item, str) else item for item in models]
        for spec in specs:
            if spec.already_downloaded():
                continue
            spec.final_path.parent.mkdir(parents=True, exist_ok=True)
            response = requests.get(spec.url, stream=True, timeout=30, headers={"Accept-Encoding": "identity"})
            response.raise_for_status()
            job = getattr(_progress_context, "job", None)
            total_size = int(response.headers.get("content-length", "0"))
            downloaded = 0
            if job:
                job.message = "Downloading %s" % spec.filename
                job.progress = 0
            try:
                with open(spec.part_path, "wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if job and total_size:
                                job.progress = max(0, min(100, int(downloaded * 100 / total_size)))
                digest = hashlib.md5()
                with open(spec.part_path, "rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != spec.md5:
                    raise RuntimeError("Checksum mismatch for %s" % spec.filename)
                os.replace(spec.part_path, spec.final_path)
            except Exception:
                if spec.part_path.exists():
                    spec.part_path.unlink()
                raise
        return True

    def _install_state(self):
        path = self.install_root / "install-state.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _selected_sam_model(self):
        return str(self.settings_mgr.get_session_setting("sam_model", "Base"))

    def sam3_status(self):
        install_state = self._install_state()
        backend = self.backend or str(install_state.get("backend") or "").lower()
        windows = os.name == "nt"
        nvidia_backend = backend in ("cu126", "cu130")
        if windows and not backend and not self.mock:
            try:
                import torch
                nvidia_backend = bool(torch.cuda.is_available())
            except Exception:
                nvidia_backend = False
        elif windows and not backend and self.mock:
            nvidia_backend = True
        supported = windows and nvidia_backend
        source_installed = (self.sam3_repo / "sam3" / "model_builder.py").is_file()
        runtime_installed = self.sam3_python.is_file()
        try:
            weights_available = (
                self.sam3_checkpoint.is_file()
                and self.sam3_checkpoint.stat().st_size == 3502755717
            )
        except OSError:
            weights_available = False
        available = supported and source_installed and runtime_installed and weights_available
        if not windows:
            reason = "SAM 3.1 currently requires Windows with an NVIDIA CUDA backend."
        elif not nvidia_backend:
            reason = "SAM 3.1 requires the NVIDIA CUDA backend; SAM 2.1 remains available."
        elif not source_installed or not runtime_installed:
            reason = "Install the optional isolated SAM 3.1 runtime."
        elif not weights_available:
            reason = "Accept gated access and install the SAM 3.1 checkpoint."
        else:
            reason = ""
        if available:
            status = "ready"
        elif not supported:
            status = "unsupported"
        elif source_installed and runtime_installed and not weights_available:
            status = "weights-pending"
        else:
            status = "not-installed"
        job = None
        if self.sam3_install_job_id in self.jobs:
            job = self.jobs[self.sam3_install_job_id].payload()
        return {
            "id": "SAM3.1",
            "label": "SAM 3.1",
            "supported": supported,
            "available": available,
            "availability": "ready" if available else "unavailable",
            "status": status,
            "reason": reason,
            "source_installed": source_installed,
            "runtime_installed": runtime_installed,
            "weights_available": weights_available,
            "source_revision": "660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7",
            "model_revision": "daa63191845a41281374e725f4c9e51c7a824460",
            "gated": True,
            "access_url": "https://huggingface.co/facebook/sam3.1",
            "capabilities": {
                "prompts": {
                    "point": True, "box": True, "text": True, "example": True,
                }
            },
            "job": job,
        }

    def models(self):
        sam3 = self.sam3_status()
        return {
            "models": [
                {
                    "id": "Base", "label": "SAM2 Base", "available": True,
                    "capabilities": {"prompts": {"point": True}},
                },
                {
                    "id": "Large", "label": "SAM2 Large", "available": True,
                    "capabilities": {"prompts": {"point": True}},
                },
                {
                    "id": "Efficient", "label": "EfficientTAM", "available": True,
                    "capabilities": {"prompts": {"point": True}},
                },
                sam3,
            ]
        }

    def health(self):
        device = "mock"
        cuda = False
        if not self.mock:
            import torch
            cuda = bool(torch.cuda.is_available())
            device = str(self.core.DeviceManager.get_device())
        return {
            "ok": True,
            "service": "samosa-ae",
            "version": VERSION,
            "mock": self.mock,
            "repo": str(self.repo),
            "device": device,
            "cuda": cuda,
            "backend_sha256": BACKEND_SHA256,
        }

    def _reset_dirs(self):
        if os.path.exists(self.core.temp_dir):
            shutil.rmtree(self.core.temp_dir)
        for folder in (
            self.core.frames_dir, self.core.mask_dir, self.core.matting_dir,
            self.core.removal_dir,
        ):
            os.makedirs(folder, exist_ok=True)

    def _write_decoded_frame(self, frame, output_frame_number, frame_format):
        array = frame.to_ndarray(format="bgr24")
        target = os.path.join(
            self.core.frames_dir, "%05d.%s" % (output_frame_number, frame_format)
        )
        if not self.cv2.imwrite(target, array):
            raise RuntimeError("Failed to write frame %d" % output_frame_number)

    def _decode_video(
        self, container, stream, frame_format, start_frame, end_frame, native_fps
    ):
        """Decode a source range and sample VFR input at AE's native frame cadence."""
        native_rate = _fraction(native_fps)
        first_target = Fraction(start_frame, 1) / native_rate
        stream_start_pts = stream.start_time if stream.start_time is not None else 0
        stream_time_base = _fraction(stream.time_base)
        if first_target > 0:
            seek_pts = stream_start_pts + int(first_target / stream_time_base)
            try:
                container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
            except Exception:
                # A freshly opened non-seekable container is already positioned at
                # the beginning, so decoding forward remains correct.
                pass

        def timed_frames():
            last_time = None
            for frame in container.decode(stream):
                if frame.pts is not None:
                    frame_time = Fraction(frame.pts - stream_start_pts) * stream_time_base
                elif last_time is None:
                    frame_time = first_target
                else:
                    frame_time = last_time + Fraction(1, 1) / native_rate
                last_time = frame_time
                yield frame, frame_time

        frames = iter(timed_frames())
        try:
            current_frame, current_time = next(frames)
        except StopIteration:
            return 0

        epsilon = Fraction(1, 1_000_000_000)
        previous_frame = None
        previous_time = None
        count = 0

        if end_frame is None:
            def remaining_frames():
                yield current_frame, current_time
                for item in frames:
                    yield item

            for frame, frame_time in remaining_frames():
                if frame_time <= first_target + epsilon:
                    previous_frame = frame
                    continue
                if previous_frame is not None:
                    self._write_decoded_frame(previous_frame, count, frame_format)
                    count += 1
                    previous_frame = None
                self._write_decoded_frame(frame, count, frame_format)
                count += 1
            if previous_frame is not None:
                self._write_decoded_frame(previous_frame, count, frame_format)
                count += 1
            return count

        for source_frame in range(start_frame, end_frame):
            # AE conforming changes each frame's duration without changing frame
            # content. Convert the effective-rate frame ordinal back to the
            # original media PTS cadence to select that same content frame.
            target_time = Fraction(source_frame, 1) / native_rate
            while current_frame is not None and current_time <= target_time + epsilon:
                previous_frame = current_frame
                previous_time = current_time
                try:
                    current_frame, current_time = next(frames)
                except StopIteration:
                    current_frame, current_time = None, None
            selected_frame = previous_frame if previous_frame is not None else current_frame
            if selected_frame is None:
                break
            if (
                current_frame is None
                and previous_time is not None
                and target_time >= previous_time + Fraction(1, 1) / native_rate - epsilon
            ):
                break
            self._write_decoded_frame(selected_frame, count, frame_format)
            count += 1
        return count

    def load_media(
        self, path: str, source_start=None, source_end=None,
        source_fps=None, source_native_fps=None,
    ):
        if (source_start is None) != (source_end is None):
            raise ApiError("source_start and source_end must be provided together")
        source = Path(path)
        if not source.exists():
            raise ApiError("Media file not found: %s" % source)
        requested_range = source_start is not None or source_end is not None
        with self.lock:
            self._close_sam3_session()
            self._reset_dirs()
            self.points = []
            self.sam_manager = None
            self.sam3_prompts = []
            self.sam3_object_ids = set()
            self.matting_manager = None
            self.removal_manager = None
            self.settings_mgr.create_new_session(str(source))
            frame_format = "png"
            suffix = source.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
                image = self.cv2.imread(str(source), self.cv2.IMREAD_COLOR)
                if image is None:
                    raise ApiError("Could not decode image: %s" % source)
                height, width = image.shape[:2]
                fps, count, color_space = 24.0, 1, 1
                start_time, end_time, start_frame, requested_end_frame = _calculate_frame_bounds(
                    source_start, source_end, fps
                )
                if requested_range and (start_frame > 0 or (requested_end_frame is not None and requested_end_frame <= 0)):
                    raise ApiError("The requested range does not contain the image frame")
                self.cv2.imwrite(os.path.join(self.core.frames_dir, "00000.png"), image)
            else:
                container = self.av.open(str(source))
                try:
                    stream = container.streams.video[0]
                    stream.thread_type = "AUTO"
                    width, height = stream.width, stream.height
                    native_fps = (
                        _finite_seconds(source_native_fps, "source_native_fps")
                        or float(stream.average_rate or 24.0)
                    )
                    fps = _finite_seconds(source_fps, "source_fps") or native_fps
                    if fps <= 0:
                        raise ApiError("source_fps must be greater than zero")
                    color_space = int(stream.codec_context.colorspace or 1)
                    start_time, end_time, start_frame, requested_end_frame = _calculate_frame_bounds(
                        source_start, source_end, fps
                    )
                    if requested_range:
                        count = self._decode_video(
                            container, stream, frame_format, start_frame,
                            requested_end_frame, native_fps,
                        )
                    else:
                        start_frame = 0
                        requested_end_frame = None
                        count = 0
                        for frame in container.decode(stream):
                            self._write_decoded_frame(frame, count, frame_format)
                            count += 1
                finally:
                    container.close()
                if count == 0:
                    raise ApiError("No video frames were decoded")
            if requested_range and count == 0:
                raise ApiError("The requested range contains no video frames")

            if (
                requested_end_frame is None
                or count < requested_end_frame - start_frame
            ):
                end_frame = start_frame + count
                end_time = float(_fraction(end_frame) / _fraction(fps))
            else:
                end_frame = requested_end_frame

            self.core.VideoInfo.width = width
            self.core.VideoInfo.height = height
            self.core.VideoInfo.fps = fps
            self.core.VideoInfo.total_frames = count
            self.core.VideoInfo.color_space = color_space
            self.settings_mgr.set_session_setting("frame_format", frame_format)
            self.settings_mgr.update_video_info(width, height, fps, count, color_space, str(source))
            self.settings_mgr.save_points([])
            self.settings_mgr.save_session_settings()
            self.loaded_path = str(source)
            self.source_start_time = float(start_time)
            self.source_end_time = float(end_time)
            self.source_start_frame = int(start_frame)
            self.source_end_frame_exclusive = int(end_frame)
            self.trimmed = requested_range
            return self.state()

    def state(self):
        settings = self.settings_mgr.session_settings
        object_ids = sorted({int(p["object_id"]) for p in self.points})
        selected_model = self._selected_sam_model()
        tracked_frames = 0
        if os.path.isdir(self.core.mask_dir):
            tracked_frames = len([
                name for name in os.listdir(self.core.mask_dir)
                if os.path.isdir(os.path.join(self.core.mask_dir, name))
                and os.listdir(os.path.join(self.core.mask_dir, name))
            ])
        return {
            "loaded": bool(self.loaded_path),
            "path": self.loaded_path,
            "width": int(self.core.VideoInfo.width),
            "height": int(self.core.VideoInfo.height),
            "fps": float(self.core.VideoInfo.fps),
            "total_frames": int(self.core.VideoInfo.total_frames),
            "source_start_time": self.source_start_time,
            "source_end_time": self.source_end_time,
            "source_start_frame": int(self.source_start_frame),
            "source_end_frame_exclusive": int(self.source_end_frame_exclusive),
            "trimmed": bool(self.trimmed),
            "points": list(self.points),
            "object_ids": object_ids,
            "settings": asdict(settings),
            "segmentation_ready": (
                self.sam3_session_id is not None
                if selected_model == "SAM3.1"
                else self.sam_manager is not None
            ),
            "tracking": {
                "frames": tracked_frames,
                "complete": bool(self.core.VideoInfo.total_frames and tracked_frames >= self.core.VideoInfo.total_frames),
            },
        }

    def update_settings(self, values):
        unknown = sorted(set(values) - self.SETTING_KEYS)
        if unknown:
            raise ApiError("Unsupported settings: %s" % ", ".join(unknown))
        previous_model = self._selected_sam_model()
        for key, value in values.items():
            self.settings_mgr.set_session_setting(key, value)
        selected_model = str(values.get("sam_model", previous_model))
        if selected_model != previous_model:
            self.sam_manager = None
            self._close_sam3_session()
            self.sam3_prompts = []
            self.sam3_object_ids = set()
            self.points = []
            self.settings_mgr.save_points([])
            if self.loaded_path:
                self._clear_segmentation_masks()
        self.settings_mgr.save_session_settings()
        return self.state()

    def _ensure_loaded(self):
        if not self.loaded_path:
            raise ApiError("Load media first")

    @staticmethod
    def _unit_coordinate(value, name):
        if isinstance(value, bool):
            raise ApiError("%s must be between 0 and 1" % name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ApiError("%s must be between 0 and 1" % name)
        if not math.isfinite(number) or number < 0 or number > 1:
            raise ApiError("%s must be between 0 and 1" % name)
        return number

    @staticmethod
    def _prompt_label(value):
        if isinstance(value, bool):
            return "positive" if value else "negative"
        label = str(value or "positive").strip().lower()
        if label in ("positive", "include", "foreground", "1"):
            return "positive"
        if label in ("negative", "exclude", "background", "0"):
            return "negative"
        raise ApiError("Prompt label must be positive or negative")

    def _clear_segmentation_masks(self):
        if os.path.exists(self.core.mask_dir):
            shutil.rmtree(self.core.mask_dir)
        os.makedirs(self.core.mask_dir, exist_ok=True)

    def _close_sam3_session(self):
        if self.sam3_bridge and self.sam3_session_id:
            try:
                self.sam3_bridge.call(
                    "close_session", {"session_id": self.sam3_session_id}, timeout=30
                )
            except Exception:
                pass
        self.sam3_session_id = None

    def close(self):
        self._close_sam3_session()
        if self.sam3_bridge:
            self.sam3_bridge.close()
            self.sam3_bridge = None

    def _prepare_sam3_frames(self):
        """Create the JPEG sequence required by the official SAM 3.1 loader."""
        frame_count = int(self.core.VideoInfo.total_frames)
        if frame_count <= 0:
            raise RuntimeError("SAM 3.1 requires at least one decoded frame")

        expected_names = {"%05d.jpg" % index for index in range(frame_count)}
        try:
            existing_names = {
                item.name for item in self.sam3_frames_dir.iterdir() if item.is_file()
            }
        except OSError:
            existing_names = set()
        if existing_names == expected_names:
            return str(self.sam3_frames_dir.resolve())

        staging = self.sam3_frames_dir.with_name(self.sam3_frames_dir.name + ".part")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        quality_key = getattr(self.cv2, "IMWRITE_JPEG_QUALITY", 1)
        try:
            for index in range(frame_count):
                source = Path(self.core.frames_dir) / ("%05d.png" % index)
                image = self.cv2.imread(str(source), getattr(self.cv2, "IMREAD_COLOR", 1))
                if image is None:
                    raise RuntimeError(
                        "Could not read decoded frame %d for SAM 3.1" % index
                    )
                target = staging / ("%05d.jpg" % index)
                if not self.cv2.imwrite(str(target), image, [quality_key, 95]):
                    raise RuntimeError(
                        "Could not prepare decoded frame %d for SAM 3.1" % index
                    )
            if self.sam3_frames_dir.exists():
                shutil.rmtree(self.sam3_frames_dir)
            os.replace(str(staging), str(self.sam3_frames_dir))
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return str(self.sam3_frames_dir.resolve())

    def _ensure_sam3_session(self):
        with self.lock:
            return self._ensure_sam3_session_locked()

    def _ensure_sam3_session_locked(self):
        self._ensure_loaded()
        if self.sam3_bridge is None:
            status = self.sam3_status()
            if not status["available"]:
                raise ApiError(status["reason"] or "SAM 3.1 is not available", 409)
            command = [
                str(self.sam3_python), "-u", str(BACKEND_DIR / "sam3_worker.py"),
                "--repo", str(self.sam3_repo),
                "--checkpoint", str(self.sam3_checkpoint),
                "--parent-pid", str(os.getpid()),
            ]
            self.sam3_bridge = Sam3Bridge(
                command, cwd=str(self.sam3_repo), startup_timeout=30,
                request_timeout=1800,
            )
            health = self.sam3_bridge.start()
            if not health.get("sam3_available"):
                reason = health.get("unavailable_reason") or "SAM 3.1 import failed"
                self.sam3_bridge.close()
                self.sam3_bridge = None
                raise ApiError(reason, 409)
        if not self.sam3_session_id:
            self.sam3_session_id = uuid.uuid4().hex
            self.sam3_transport_dir.mkdir(parents=True, exist_ok=True)
            resource_path = self._prepare_sam3_frames()
            try:
                self.sam3_bridge.call(
                    "start_session",
                    {
                        "session_id": self.sam3_session_id,
                        "resource_path": resource_path,
                        "mask_root": str(self.sam3_transport_dir.resolve()),
                    },
                    timeout=1800,
                )
            except Exception:
                self.sam3_session_id = None
                raise
        return self.sam3_bridge

    def _decode_sam3_array(self, value):
        if isinstance(value, list):
            return self.np.asarray(value)
        if not isinstance(value, dict):
            return self.np.asarray(value)
        encoding = value.get("encoding")
        if encoding not in ("raw", "base64"):
            return self.np.asarray(value)
        try:
            dtype = self.np.dtype(value["dtype"])
            shape = tuple(int(item) for item in value["shape"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("SAM 3.1 returned invalid array metadata")
        if any(item < 0 for item in shape):
            raise RuntimeError("SAM 3.1 returned invalid array dimensions")
        if encoding == "raw":
            path = Path(value.get("path", "")).resolve()
            root = self.sam3_transport_dir.resolve()
            try:
                path.relative_to(root)
            except ValueError:
                raise RuntimeError("SAM 3.1 returned an unsafe transport path")
            try:
                data = path.read_bytes()
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        else:
            try:
                data = base64.b64decode(value.get("data", ""), validate=True)
            except (ValueError, TypeError):
                raise RuntimeError("SAM 3.1 returned invalid base64 data")
        expected = int(self.np.prod(shape, dtype=self.np.int64)) * dtype.itemsize
        if len(data) != expected:
            raise RuntimeError("SAM 3.1 returned a truncated array")
        return self.np.frombuffer(data, dtype=dtype).reshape(shape)

    @staticmethod
    def _sam3_outputs(result):
        current = result
        for _ in range(4):
            if not isinstance(current, dict):
                return {}
            if isinstance(current.get("outputs"), dict):
                return current["outputs"]
            if isinstance(current.get("result"), dict):
                current = current["result"]
                continue
            return current
        return {}

    def _write_sam3_result(self, result, fallback_frame):
        outputs = self._sam3_outputs(result)
        masks_value = outputs.get("out_binary_masks")
        if masks_value is None:
            return []
        masks = self._decode_sam3_array(masks_value)
        if masks.size == 0:
            object_ids = outputs.get("out_obj_ids")
            if object_ids is not None and self._decode_sam3_array(object_ids).size:
                raise RuntimeError("SAM 3.1 returned mismatched mask and object counts")
            return []
        while masks.ndim > 3 and masks.shape[1] == 1:
            masks = masks[:, 0]
        if masks.ndim == 2:
            masks = masks[None, ...]
        if masks.ndim != 3:
            raise RuntimeError("SAM 3.1 returned masks with an unsupported shape")
        object_ids = outputs.get("out_obj_ids")
        object_ids = (
            [int(item) for item in self._decode_sam3_array(object_ids).reshape(-1)]
            if object_ids is not None
            else list(range(masks.shape[0]))
        )
        if len(object_ids) != masks.shape[0]:
            raise RuntimeError("SAM 3.1 returned mismatched mask and object counts")
        frame_number = int(
            result.get("frame_index", fallback_frame)
            if isinstance(result, dict) else fallback_frame
        )
        if frame_number < 0 or frame_number >= self.core.VideoInfo.total_frames:
            raise RuntimeError("SAM 3.1 returned an invalid frame index")
        frame_dir = Path(self.core.mask_dir) / ("%05d" % frame_number)
        frame_dir.mkdir(parents=True, exist_ok=True)
        for object_id, mask in zip(object_ids, masks):
            encoded = (mask > 0).astype(self.np.uint8) * 255
            if encoded.shape != (
                self.core.VideoInfo.height, self.core.VideoInfo.width
            ):
                encoded = self.cv2.resize(
                    encoded,
                    (self.core.VideoInfo.width, self.core.VideoInfo.height),
                    interpolation=self.cv2.INTER_NEAREST,
                )
            if not self.cv2.imwrite(str(frame_dir / ("%d.png" % object_id)), encoded):
                raise RuntimeError("Could not save SAM 3.1 mask")
        return object_ids

    def _rebuild_sam3_point_records(self):
        real = []
        for prompt in self.sam3_prompts:
            if prompt["type"] != "point":
                continue
            real.append({
                "frame": prompt["frame"],
                "object_id": prompt["object_id"],
                "positive": prompt["label"] == "positive",
                "x": int(round(prompt["x"] * max(0, self.core.VideoInfo.width - 1))),
                "y": int(round(prompt["y"] * max(0, self.core.VideoInfo.height - 1))),
            })
        real_ids = {point["object_id"] for point in real}
        synthetic = [
            {
                "frame": 0, "object_id": object_id, "positive": True,
                "x": -1, "y": -1, "synthetic": True,
            }
            for object_id in sorted(self.sam3_object_ids - real_ids)
        ]
        self.points = real + synthetic
        self.settings_mgr.save_points(self.points)

    def _worker_prompt(self, prompt, history):
        prompt_type = prompt["type"]
        if prompt_type == "point":
            matching = [
                item for item in history
                if item["type"] == "point"
                and item["frame"] == prompt["frame"]
                and item["object_id"] == prompt["object_id"]
            ]
            return {
                "type": "point",
                "points": [[item["x"], item["y"]] for item in matching],
                "labels": [item["label"] for item in matching],
            }
        if prompt_type == "example":
            return {
                "type": "example",
                "examples": [
                    {
                        "box": [item["x1"], item["y1"], item["x2"], item["y2"]],
                        "label": item["label"],
                    }
                    for item in history if item["type"] == "example"
                ],
            }
        if prompt_type == "box":
            return {
                "type": "box",
                "box": [prompt["x1"], prompt["y1"], prompt["x2"], prompt["y2"]],
                "label": "positive",
            }
        return {"type": "text", "text": prompt["text"]}

    def _apply_sam3_prompt(self, prompt, record=True):
        bridge = self._ensure_sam3_session()
        prompt_type = prompt["type"]
        previous_history = list(self.sam3_prompts)
        history = list(previous_history)
        reset_session = bool(previous_history) and (
            prompt_type in ("text", "box")
            or (
                prompt_type == "example"
                and any(item["type"] != "example" for item in previous_history)
            )
        )
        if prompt_type in ("text", "box"):
            history = [prompt]
        elif prompt_type == "example":
            history = [item for item in history if item["type"] == "example"]
            history.append(prompt)
        else:
            history.append(prompt)
        if reset_session:
            bridge.call(
                "reset_session", {"session_id": self.sam3_session_id}, timeout=30
            )
        if prompt_type in ("text", "box", "example"):
            self._clear_segmentation_masks()
            self.sam3_object_ids = set()
        result = bridge.call(
            "add_prompt",
            {
                "session_id": self.sam3_session_id,
                "frame_index": prompt["frame"],
                "obj_id": prompt["object_id"],
                "prompt": self._worker_prompt(prompt, history),
            },
            timeout=1800,
        )
        object_ids = self._write_sam3_result(result, prompt["frame"])
        if prompt_type in ("text", "box", "example"):
            self.sam3_object_ids = set(object_ids)
        else:
            self.sam3_object_ids.update(object_ids or [prompt["object_id"]])
        if record:
            self.sam3_prompts = history
        self._rebuild_sam3_point_records()
        return object_ids

    def sam3_prompt(self, payload):
        self._ensure_loaded()
        if self._selected_sam_model() != "SAM3.1":
            raise ApiError("Select SAM 3.1 before using SAM 3.1 prompts")
        prompt_type = str(payload.get("type") or "").strip().lower()
        if prompt_type not in ("point", "box", "text", "example"):
            raise ApiError("Unsupported SAM 3.1 prompt type")
        frame = int(payload.get("frame", 0))
        object_id = int(payload.get("object_id", 0))
        if frame < 0 or frame >= self.core.VideoInfo.total_frames:
            raise ApiError("Frame is outside the clip")
        prompt = {"type": prompt_type, "frame": frame, "object_id": object_id}
        if prompt_type == "point":
            prompt.update(
                x=self._unit_coordinate(payload.get("x"), "x"),
                y=self._unit_coordinate(payload.get("y"), "y"),
                label=self._prompt_label(payload.get("label")),
            )
        elif prompt_type in ("box", "example"):
            prompt.update(
                x1=self._unit_coordinate(payload.get("x1"), "x1"),
                y1=self._unit_coordinate(payload.get("y1"), "y1"),
                x2=self._unit_coordinate(payload.get("x2"), "x2"),
                y2=self._unit_coordinate(payload.get("y2"), "y2"),
                label="positive" if prompt_type == "box" else self._prompt_label(payload.get("label")),
            )
            if prompt["x2"] <= prompt["x1"] or prompt["y2"] <= prompt["y1"]:
                raise ApiError("Prompt box must have positive width and height")
        else:
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ApiError("Text prompt must not be empty")
            prompt["text"] = text
        try:
            object_ids = self._apply_sam3_prompt(prompt)
        except Sam3BridgeError as exc:
            raise ApiError(str(exc), 409)
        return {"ok": True, "object_ids": object_ids, "state": self.state()}

    @staticmethod
    def _redact_token(message, token):
        safe = str(message or "")
        if token:
            safe = safe.replace(token, "[redacted]")
        return re.sub(r"hf_[A-Za-z0-9_-]+", "[redacted]", safe)

    def setup_sam3(self, payload):
        if not bool(payload.get("accept_license")):
            raise ApiError("Accept the Meta SAM License before installing SAM 3.1")
        token = str(payload.get("hf_token") or "").strip()
        if not token:
            raise ApiError("A Hugging Face token is required for gated SAM 3.1 access")
        status = self.sam3_status()
        if not status["supported"]:
            raise ApiError(status["reason"], 409)
        manager = self.install_root / "installer" / "manage-sam31.ps1"
        if not manager.is_file():
            raise ApiError("The SAM 3.1 setup tool is not installed", 409)
        if self.sam3_install_job_id in self.jobs:
            existing = self.jobs[self.sam3_install_job_id]
            if existing.status in ("queued", "running"):
                return existing.payload()
        backend = self.backend or str(self._install_state().get("backend") or "cu130")

        def run(job):
            environment = os.environ.copy()
            environment["HF_TOKEN"] = token
            powershell = Path(
                os.environ.get("SystemRoot", r"C:\Windows")
            ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            command = [
                str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(manager), "-InstallRoot", str(self.install_root),
                "-Backend", backend, "-Install", "-DownloadModel",
                "-AcceptMetaSamLicense",
            ]
            job.message = "Preparing isolated SAM 3.1 runtime"
            job.progress = 2
            process = subprocess.Popen(
                command,
                cwd=str(self.install_root),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            job.process = process
            recent_output = deque(maxlen=40)
            setup_error = None
            try:
                for line in process.stdout or ():
                    safe = self._redact_token(line.strip(), token)
                    if not safe:
                        continue
                    recent_output.append(safe)
                    if safe.startswith("SAMOSA_SAM31 "):
                        try:
                            event = json.loads(safe[len("SAMOSA_SAM31 "):])
                            name = event.get("event")
                            if name == "started":
                                job.message = "Downloading SAM 3.1 checkpoint"
                                job.progress = max(job.progress, 20)
                            elif name == "complete":
                                job.message = "Verifying SAM 3.1 checkpoint"
                                job.progress = max(job.progress, 95)
                            elif name == "error":
                                setup_error = self._redact_token(
                                    event.get("message") or "SAM 3.1 setup failed", token
                                ).strip()
                                job.message = setup_error
                        except (TypeError, ValueError):
                            pass
                    elif "Downloading pinned SAM 3.1 source" in safe:
                        job.message = "Downloading pinned SAM 3.1 source"
                        job.progress = max(job.progress, 5)
                return_code = process.wait()
            finally:
                job.process = None
                environment.pop("HF_TOKEN", None)
            if job.cancelled:
                return {"cancelled": True}
            if return_code != 0:
                diagnostic = " | ".join(list(recent_output)[-8:])
                raise RuntimeError(setup_error or diagnostic or "SAM 3.1 setup failed")
            if self.sam3_bridge:
                self.sam3_bridge.close()
                self.sam3_bridge = None
                self.sam3_session_id = None
            refreshed = self.sam3_status()
            if not refreshed["available"]:
                raise RuntimeError(refreshed["reason"] or "SAM 3.1 setup is incomplete")
            return {
                "available": True,
                "source_revision": refreshed["source_revision"],
                "model_revision": refreshed["model_revision"],
            }

        job = self._new_job("sam3 setup", run)
        self.sam3_install_job_id = job["id"]
        return job

    def _ensure_segmenter(self):
        self._ensure_loaded()
        if self._selected_sam_model() == "SAM3.1":
            self._ensure_sam3_session()
            return
        if self.sam_manager is None:
            self.sam_manager = self.sammie_api.SamManager()
            if not self.mock:
                model = self.settings_mgr.get_session_setting("sam_model", "Base")
                if not self.sam_manager.load_segmentation_model(model=model, parent_window=None):
                    self.sam_manager = None
                    raise RuntimeError("Could not load segmentation model")
                self.sam_manager.initialize_predictor()

    def _mock_mask(self, frame: int, object_id: int):
        base = self.core.load_base_frame(frame)
        if base is None:
            raise ApiError("Frame does not exist")
        mask = self.np.zeros(base.shape[:2], dtype=self.np.uint8)
        radius = max(8, min(base.shape[0], base.shape[1]) // 8)
        for point in self.points:
            if point["frame"] != frame or point["object_id"] != object_id:
                continue
            color = 255 if point["positive"] else 0
            self.cv2.circle(mask, (point["x"], point["y"]), radius, color, -1)
        target = os.path.join(self.core.mask_dir, "%05d" % frame, "%d.png" % object_id)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.cv2.imwrite(target, mask)

    def add_point(self, payload):
        if self._selected_sam_model() == "SAM3.1":
            width = max(1, int(self.core.VideoInfo.width) - 1)
            height = max(1, int(self.core.VideoInfo.height) - 1)
            return self.sam3_prompt({
                "type": "point",
                "frame": payload.get("frame", 0),
                "object_id": payload.get("object_id", 0),
                "x": float(payload["x"]) / width,
                "y": float(payload["y"]) / height,
                "label": "positive" if payload.get("positive", True) else "negative",
            })["state"]
        self._ensure_segmenter()
        frame = int(payload.get("frame", 0))
        object_id = int(payload.get("object_id", 0))
        x, y = int(payload["x"]), int(payload["y"])
        positive = bool(payload.get("positive", True))
        if frame < 0 or frame >= self.core.VideoInfo.total_frames:
            raise ApiError("Frame is outside the clip")
        if x < 0 or y < 0 or x >= self.core.VideoInfo.width or y >= self.core.VideoInfo.height:
            raise ApiError("Point is outside the frame")
        point = {"frame": frame, "object_id": object_id, "positive": positive, "x": x, "y": y}
        self.points.append(point)
        self.settings_mgr.save_points(self.points)
        if self.mock:
            self._mock_mask(frame, object_id)
        else:
            selected = [p for p in self.points if p["frame"] == frame and p["object_id"] == object_id]
            coords = self.np.array([[p["x"], p["y"]] for p in selected], dtype=self.np.float32)
            labels = self.np.array([1 if p["positive"] else 0 for p in selected], dtype=self.np.int32)
            self.sam_manager.segment_image(frame, object_id, coords, labels)
        return self.state()

    def undo_point(self):
        self._ensure_loaded()
        if self._selected_sam_model() == "SAM3.1":
            if not self.sam3_prompts:
                return self.state()
            history = list(self.sam3_prompts[:-1])
            self._replay_sam3_history(history)
            return self.state()
        if not self.points:
            return self.state()
        removed = self.points.pop()
        self.settings_mgr.save_points(self.points)
        frame, object_id = removed["frame"], removed["object_id"]
        if self.mock:
            self._mock_mask(frame, object_id)
        elif self.sam_manager:
            self.sam_manager.clear_tracking()
            self.sam_manager.replay_points(self.points)
        return self.state()

    def clear_points(self):
        self._ensure_loaded()
        if self._selected_sam_model() == "SAM3.1":
            self._close_sam3_session()
            self.sam3_prompts = []
            self.sam3_object_ids = set()
            self.points = []
            self.settings_mgr.save_points([])
            self._clear_segmentation_masks()
            return self.state()
        self.points = []
        self.settings_mgr.save_points([])
        if os.path.exists(self.core.mask_dir):
            shutil.rmtree(self.core.mask_dir)
        os.makedirs(self.core.mask_dir, exist_ok=True)
        if self.sam_manager and not self.mock:
            self.sam_manager.predictor.reset_state(self.sam_manager.inference_state)
        return self.state()

    def clear_tracking(self):
        self._ensure_loaded()
        if self._selected_sam_model() == "SAM3.1":
            self._replay_sam3_history(list(self.sam3_prompts))
            return self.state()
        if os.path.exists(self.core.mask_dir):
            shutil.rmtree(self.core.mask_dir)
        os.makedirs(self.core.mask_dir, exist_ok=True)
        if self.sam_manager and not self.mock:
            self.sam_manager.predictor.reset_state(self.sam_manager.inference_state)
            if self.points:
                self.sam_manager.replay_points(self.points)
        elif self.mock:
            keyed = {(p["frame"], p["object_id"]) for p in self.points}
            for frame_number, object_id in keyed:
                self._mock_mask(frame_number, object_id)
        return self.state()

    def _replay_sam3_history(self, history):
        self._close_sam3_session()
        self._clear_segmentation_masks()
        self.sam3_prompts = []
        self.sam3_object_ids = set()
        self.points = []
        self.settings_mgr.save_points([])
        for prompt in history:
            self._apply_sam3_prompt(dict(prompt))

    def frame_png(self, frame: int, view: str, object_id=None):
        self._ensure_loaded()
        frame = max(0, min(int(frame), self.core.VideoInfo.total_frames - 1))
        view_modes = {
            "edit": "Segmentation-Edit", "segmentation-matte": "Segmentation-Matte",
            "segmentation-alpha": "Segmentation-Alpha", "segmentation-bgcolor": "Segmentation-BGcolor",
            "matting-matte": "Matting-Matte", "matting-alpha": "Matting-Alpha",
            "matting-bgcolor": "Matting-BGcolor", "removal": "ObjectRemoval", "source": "None",
        }
        mode = view_modes.get(view, "Segmentation-Edit")
        options = self.settings_mgr.get_view_options()
        options["view_mode"] = mode
        filter_id = None if object_id in (None, "", -1, "-1") else int(object_id)
        array = self.sammie_api.update_image(
            frame, options, self.points, return_numpy=True, object_id_filter=filter_id
        )
        if array is None:
            raise ApiError("Frame is unavailable", 404)
        if len(array.shape) == 2:
            encoded_array = array
        elif array.shape[2] == 4:
            encoded_array = self.cv2.cvtColor(array, self.cv2.COLOR_RGBA2BGRA)
        else:
            encoded_array = self.cv2.cvtColor(array, self.cv2.COLOR_RGB2BGR)
        ok, encoded = self.cv2.imencode(".png", encoded_array)
        if not ok:
            raise RuntimeError("Could not encode preview")
        return encoded.tobytes()

    def _new_job(self, kind, target):
        with self.lock:
            active = [j for j in self.jobs.values() if j.status in ("queued", "running")]
            if active:
                raise ApiError("Another processing job is already running", 409)
            job = Job(kind)
            self.jobs[job.id] = job
            NullProgressDialog.cancelled = False

        def runner():
            job.status = "running"
            job.message = "Running %s" % kind
            _progress_context.job = job
            try:
                result = target(job)
                if job.cancelled:
                    job.status = "cancelled"
                    job.message = "Cancelled"
                else:
                    job.result = result
                    job.progress = 100
                    job.status = "complete"
                    job.message = "%s complete" % kind.capitalize()
            except Exception as exc:
                job.error = "%s\n%s" % (exc, traceback.format_exc())
                job.message = str(exc)
                job.status = "failed"
            finally:
                _progress_context.job = None

        threading.Thread(target=runner, daemon=True).start()
        return job.payload()

    def get_job(self, job_id):
        if job_id not in self.jobs:
            raise ApiError("Job not found", 404)
        return self.jobs[job_id].payload()

    def cancel_job(self, job_id):
        if job_id not in self.jobs:
            raise ApiError("Job not found", 404)
        self.jobs[job_id].cancelled = True
        self.jobs[job_id].cancel_event.set()
        process = self.jobs[job_id].process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        NullProgressDialog.cancelled = True
        return self.jobs[job_id].payload()

    def propagate(self):
        self._ensure_segmenter()

        def run(job):
            if self._selected_sam_model() == "SAM3.1":
                if not self.sam3_prompts:
                    raise ApiError("Add at least one SAM 3.1 prompt first")
                self._clear_segmentation_masks()

                def on_event(message):
                    event = message.get("result", message)
                    frame_number = int(
                        event.get("frame_index", 0)
                        if isinstance(event, dict) else 0
                    )
                    object_ids = self._write_sam3_result(event, frame_number)
                    self.sam3_object_ids.update(object_ids)
                    self._rebuild_sam3_point_records()
                    total = max(1, self.core.VideoInfo.total_frames)
                    job.progress = max(
                        job.progress, min(99, int((frame_number + 1) * 100 / total))
                    )
                    job.message = "Tracking frame %d of %d" % (
                        frame_number + 1, total
                    )

                result = self.sam3_bridge.propagate(
                    {"session_id": self.sam3_session_id},
                    on_event=on_event,
                    cancel_event=job.cancel_event,
                    timeout=7200,
                )
                if result.get("cancelled"):
                    job.cancelled = True
                return {"frames": self.core.VideoInfo.total_frames}
            if self.mock:
                source_masks = {}
                for point in self.points:
                    key = point["object_id"]
                    mask_path = os.path.join(self.core.mask_dir, "%05d" % point["frame"], "%d.png" % key)
                    if os.path.exists(mask_path):
                        source_masks[key] = self.cv2.imread(mask_path, self.cv2.IMREAD_GRAYSCALE)
                for frame in range(self.core.VideoInfo.total_frames):
                    if job.cancelled: break
                    for object_id, mask in source_masks.items():
                        target = os.path.join(self.core.mask_dir, "%05d" % frame, "%d.png" % object_id)
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        self.cv2.imwrite(target, mask)
                    job.progress = int((frame + 1) * 100 / self.core.VideoInfo.total_frames)
                return {"frames": self.core.VideoInfo.total_frames}
            result = self.sam_manager.track_objects(ParentShim(self.settings_mgr))
            if not result:
                raise RuntimeError("Propagation cancelled")
            return {"frames": self.core.VideoInfo.total_frames}

        return self._new_job("propagation", run)

    def run_matting(self):
        self._ensure_loaded()
        if not self.points:
            raise ApiError("Add at least one object point first")

        def run(job):
            if self.mock:
                for frame in range(self.core.VideoInfo.total_frames):
                    src_dir = os.path.join(self.core.mask_dir, "%05d" % frame)
                    dst_dir = os.path.join(self.core.matting_dir, "%05d" % frame)
                    os.makedirs(dst_dir, exist_ok=True)
                    if os.path.isdir(src_dir):
                        for name in os.listdir(src_dir):
                            mask = self.cv2.imread(os.path.join(src_dir, name), self.cv2.IMREAD_GRAYSCALE)
                            mask = self.cv2.GaussianBlur(mask, (7, 7), 0)
                            self.cv2.imwrite(os.path.join(dst_dir, name), mask)
                    job.progress = int((frame + 1) * 100 / self.core.VideoInfo.total_frames)
                return {"model": "mock"}
            self.matting_manager = self.create_matting_manager()
            if self.matting_manager.load_matting_model(parent_window=None) is None:
                raise RuntimeError("Could not load matting model")
            combined = bool(self.settings_mgr.get_session_setting("matany_combined", False))
            result = self.matting_manager.run_matting(self.points, ParentShim(self.settings_mgr), combined=combined)
            if result in (0, False):
                raise RuntimeError("Matting cancelled or failed")
            return {"model": self.settings_mgr.get_session_setting("matany_model")}

        return self._new_job("matting", run)

    def deduplicate(self, threshold):
        self._ensure_loaded()

        def run(job):
            if self.mock:
                return {"threshold": float(threshold)}
            import sammie.duplicate_frame_handler as dedupe_mod
            if not dedupe_mod.replace_similar_matte_frames(ParentShim(self.settings_mgr), float(threshold)):
                raise RuntimeError("Deduplication failed")
            return {"threshold": float(threshold)}

        return self._new_job("deduplication", run)

    def run_removal(self, method):
        self._ensure_loaded()
        if not self.points:
            raise ApiError("Add at least one object point first")

        def run(job):
            self.removal_manager = self.RemovalManager()
            if method == "OpenCV" or self.mock:
                result = self.removal_manager.run_object_removal_cv(self.points, ParentShim(self.settings_mgr))
            else:
                result = self.removal_manager.run_object_removal_minimax(self.points, ParentShim(self.settings_mgr))
            if result in (0, False):
                raise RuntimeError("Object removal cancelled or failed")
            self.settings_mgr.set_session_setting("show_removal_mask", False)
            self.settings_mgr.save_session_settings()
            return {"method": method}

        return self._new_job("removal", run)

    @staticmethod
    def _filename_token(value, fallback):
        token = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or ""))
        token = re.sub(r"\s+", "_", token).strip(" ._")
        return token or fallback

    def _default_export_name(self, requested_name, object_id, output_type):
        clip_name = requested_name or Path(self.loaded_path).stem
        clip_token = self._filename_token(clip_name, "clip")
        if object_id == -1:
            object_token = "all"
        else:
            object_names = self.settings_mgr.get_session_setting("object_names", {}) or {}
            object_name = object_names.get(str(object_id), "Object %d" % (object_id + 1))
            object_token = self._filename_token(object_name, "object_%d" % (object_id + 1))
        output_token = OUTPUT_TAGS.get(output_type, self._filename_token(output_type, "output").lower())
        return "%s_%s_%s" % (clip_token, object_token, output_token)

    def export(self, payload):
        self._ensure_loaded()
        output_type = payload.get("output_type", "Segmentation-Alpha")
        format_id = payload.get("format", "png")
        object_id = int(payload.get("object_id", -1))
        quality = int(payload.get("quality", 14))
        base = self._default_export_name(str(payload.get("name") or "").strip(), object_id, output_type)
        requested_dir = str(payload.get("output_dir", "")).strip()
        if requested_dir:
            destination = Path(requested_dir).expanduser()
            if not destination.is_absolute():
                raise ApiError("Export location must be an absolute path")
            if destination.exists() and not destination.is_dir():
                raise ApiError("Export location is not a folder: %s" % destination)
        else:
            destination = self.repo / "temp" / "ae_exports"
        output_root = destination / (base + "_" + uuid.uuid4().hex[:8])
        output_root.mkdir(parents=True, exist_ok=True)

        def run(job):
            from fractions import Fraction
            from sammie.export_formats import FormatRegistry
            fmt = FormatRegistry.get_format(format_id)
            if output_type not in fmt.get_available_output_types():
                raise ApiError("%s does not support %s" % (fmt.display_name, output_type))
            if fmt.is_sequence and format_id == "png":
                first = None
                frame_count = 0
                for frame_number in range(self.core.VideoInfo.total_frames):
                    if job.cancelled:
                        break
                    array = self._export_frame(frame_number, output_type, object_id)
                    target = output_root / ("%s.%04d.png" % (base, frame_number))
                    if array.ndim == 2:
                        encoded = array
                    elif array.shape[2] == 4:
                        encoded = self.cv2.cvtColor(array, self.cv2.COLOR_RGBA2BGRA)
                    else:
                        encoded = self.cv2.cvtColor(array, self.cv2.COLOR_RGB2BGR)
                    if not self.cv2.imwrite(str(target), encoded, [self.cv2.IMWRITE_PNG_COMPRESSION, 4]):
                        raise RuntimeError("Could not write %s" % target)
                    if first is None:
                        first = target
                    frame_count += 1
                    job.progress = int((frame_number + 1) * 100 / self.core.VideoInfo.total_frames)
                if first is None:
                    raise RuntimeError("Exporter produced no files")
                return {
                    "path": str(first), "sequence": True, "folder": str(output_root),
                    "fps": float(self.core.VideoInfo.fps), "frame_count": frame_count,
                    "trimmed": bool(self.trimmed),
                    "source_start_time": self.source_start_time,
                    "source_end_time": self.source_end_time,
                }
            if fmt.is_sequence:
                from sammie.export_formats import ExportSettings
                from sammie.export_workers import SequenceExportWorker
                settings = ExportSettings(
                    format_id=format_id, output_dir=str(output_root), filename_template=base,
                    output_type=output_type, object_id=object_id, antialias=True,
                    quality=quality, use_inout=False, in_point=None, out_point=None,
                )
                worker = SequenceExportWorker(settings, self.points, self.core.VideoInfo.total_frames, base)
                messages = []
                worker.finished.connect(lambda ok, message: messages.append((ok, message)))
                worker.run()
                files = sorted(output_root.glob("*" + fmt.file_extension))
                if not files:
                    detail = messages[-1][1] if messages else "Exporter produced no files"
                    raise RuntimeError(detail)
                return {
                    "path": str(files[0]), "sequence": True, "folder": str(output_root),
                    "fps": float(self.core.VideoInfo.fps), "frame_count": len(files),
                    "trimmed": bool(self.trimmed),
                    "source_start_time": self.source_start_time,
                    "source_end_time": self.source_end_time,
                }
            output_path = output_root / (base + fmt.file_extension)
            container = self.av.open(str(output_path), mode="w")
            fps = Fraction(self.core.VideoInfo.fps).limit_denominator(1001)
            stream = container.add_stream(fmt.get_codec_name(), rate=fps)
            stream.width = self.core.VideoInfo.width
            stream.height = self.core.VideoInfo.height
            has_alpha = "Alpha" in output_type
            stream.pix_fmt = fmt.get_pixel_format(has_alpha)
            for key, value in fmt.get_codec_options(quality).items():
                stream.options[key] = str(value)
            frame_count = 0
            try:
                for frame_number in range(self.core.VideoInfo.total_frames):
                    if job.cancelled:
                        break
                    array = self._export_frame(frame_number, output_type, object_id)
                    if has_alpha and array.shape[2] != 4:
                        alpha = self.np.full(array.shape[:2] + (1,), 255, dtype=self.np.uint8)
                        array = self.np.concatenate([array, alpha], axis=2)
                    if not has_alpha and array.shape[2] == 4:
                        array = array[:, :, :3]
                    av_frame = self.av.VideoFrame.from_ndarray(array, format="rgba" if has_alpha else "rgb24")
                    av_frame.pts = frame_number
                    for packet in stream.encode(av_frame):
                        container.mux(packet)
                    frame_count += 1
                    job.progress = int((frame_number + 1) * 100 / self.core.VideoInfo.total_frames)
                for packet in stream.encode():
                    container.mux(packet)
            finally:
                container.close()
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Exporter produced no file")
            return {
                "path": str(output_path), "sequence": False, "folder": str(output_root),
                "fps": float(self.core.VideoInfo.fps), "frame_count": frame_count,
                "trimmed": bool(self.trimmed),
                "source_start_time": self.source_start_time,
                "source_end_time": self.source_end_time,
            }

        return self._new_job("export", run)

    def _export_frame(self, frame_number, output_type, object_id):
        options = {"view_mode": output_type, "antialias": True, "show_removal_mask": False}
        if output_type.endswith("BGcolor"):
            options["bgcolor"] = self.settings_mgr.get_session_setting("bgcolor", (0, 255, 0))
        filter_id = None if object_id == -1 else object_id
        array = self.sammie_api.update_image(
            frame_number, options, self.points, return_numpy=True, object_id_filter=filter_id
        )
        if array is None:
            raise RuntimeError("Could not render frame %d for %s" % (frame_number, output_type))
        return array


class Handler(BaseHTTPRequestHandler):
    engine = None
    server_version = "SamosaAE/" + VERSION

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            raise ApiError("Request body must be valid JSON")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/health":
                health = self.engine.health()
                health["clients"] = self.server.client_registry.count()
                return self._json(health)
            if parsed.path == "/api/state":
                return self._json(self.engine.state())
            if parsed.path == "/api/models":
                return self._json(self.engine.models())
            if parsed.path == "/api/sam3/status":
                return self._json(self.engine.sam3_status())
            if parsed.path == "/api/job":
                return self._json(self.engine.get_job(query.get("id", [""])[0]))
            if parsed.path == "/api/frame":
                data = self.engine.frame_png(
                    int(query.get("frame", [0])[0]), query.get("view", ["edit"])[0],
                    query.get("object_id", [None])[0],
                )
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            raise ApiError("Endpoint not found", 404)
        except ApiError as exc:
            self._json({"ok": False, "error": str(exc)}, exc.status)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc), "trace": traceback.format_exc()}, 500)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            body = self._body()
            if parsed.path == "/api/client/register":
                count = self.server.client_registry.register(
                    body["client_token"], body["parent_pid"]
                )
                return self._json({"ok": True, "clients": count})
            if parsed.path == "/api/client/release":
                count = self.server.client_registry.release(body["client_token"])
                self._json({"ok": True, "clients": count})
                if count == 0:
                    threading.Thread(
                        target=shutdown_if_unused,
                        args=(self.server, self.server.client_registry),
                        daemon=True,
                    ).start()
                return
            routes = {
                "/api/load": lambda: self.engine.load_media(
                    body["path"], body.get("source_start"), body.get("source_end"),
                    body.get("source_fps"), body.get("source_native_fps"),
                ),
                "/api/settings": lambda: self.engine.update_settings(body),
                "/api/points": lambda: self.engine.add_point(body),
                "/api/points/undo": self.engine.undo_point,
                "/api/points/clear": self.engine.clear_points,
                "/api/sam3/prompt": lambda: self.engine.sam3_prompt(body),
                "/api/sam3/setup": lambda: self.engine.setup_sam3(body),
                "/api/tracking/clear": self.engine.clear_tracking,
                "/api/propagate": self.engine.propagate,
                "/api/dedupe": lambda: self.engine.deduplicate(body.get("threshold", 0.8)),
                "/api/matting": self.engine.run_matting,
                "/api/removal": lambda: self.engine.run_removal(body.get("method", "MiniMax-Remover")),
                "/api/export": lambda: self.engine.export(body),
                "/api/job/cancel": lambda: self.engine.cancel_job(body["id"]),
            }
            if parsed.path == "/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if parsed.path not in routes:
                raise ApiError("Endpoint not found", 404)
            self._json(routes[parsed.path]())
        except KeyError as exc:
            self._json({"ok": False, "error": "Missing field: %s" % exc}, 400)
        except ApiError as exc:
            self._json({"ok": False, "error": str(exc)}, exc.status)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc), "trace": traceback.format_exc()}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("SAMMIE_REPO"))
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mock", action="store_true", default=os.environ.get("SAMOSA_MOCK") == "1")
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--client-token", default=None)
    parser.add_argument("--install-root", default=os.environ.get("SAMOSA_INSTALL_ROOT"))
    parser.add_argument("--backend", default=os.environ.get("SAMOSA_BACKEND"))
    parser.add_argument("--sam3-python", default=os.environ.get("SAMOSA_SAM3_PYTHON"))
    parser.add_argument("--sam3-repo", default=os.environ.get("SAMOSA_SAM3_REPO"))
    parser.add_argument(
        "--sam3-checkpoint", default=os.environ.get("SAMOSA_SAM3_CHECKPOINT")
    )
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or SAMMIE_REPO is required")
    Handler.engine = Engine(
        Path(args.repo),
        mock=args.mock,
        sam3_python=args.sam3_python,
        sam3_repo=args.sam3_repo,
        sam3_checkpoint=args.sam3_checkpoint,
        install_root=args.install_root,
        backend=args.backend,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.client_registry = ClientRegistry()
    if args.parent_pid:
        server.client_registry.register(
            args.client_token or ("parent-%d" % args.parent_pid), args.parent_pid
        )
    print("Samosa AE service listening on http://%s:%d" % (args.host, args.port), flush=True)
    watchdog = start_client_watchdog(server, server.client_registry)
    try:
        server.serve_forever()
    finally:
        watchdog[0].set()
        Handler.engine.close()
        server.server_close()


if __name__ == "__main__":
    main()
