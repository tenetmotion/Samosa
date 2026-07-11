#!/usr/bin/env python3
"""Headless Samosa bridge to a local Sammie-Roto-2 checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
DEFAULT_PORT = 43831
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


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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

    def __init__(self, repo: Path, mock: bool = False):
        self.repo = repo.resolve()
        self.mock = mock
        self.lock = threading.RLock()
        self.jobs = {}
        self.loaded_path = None
        self.points = []
        self.sam_manager = None
        self.matting_manager = None
        self.removal_manager = None
        self._load_modules()

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
            "version": "1.2.0",
            "mock": self.mock,
            "repo": str(self.repo),
            "device": device,
            "cuda": cuda,
        }

    def _reset_dirs(self):
        if os.path.exists(self.core.temp_dir):
            shutil.rmtree(self.core.temp_dir)
        for folder in (
            self.core.frames_dir, self.core.mask_dir, self.core.matting_dir,
            self.core.removal_dir,
        ):
            os.makedirs(folder, exist_ok=True)

    def load_media(self, path: str):
        source = Path(path)
        if not source.exists():
            raise ApiError("Media file not found: %s" % source)
        with self.lock:
            self._reset_dirs()
            self.points = []
            self.sam_manager = None
            self.matting_manager = None
            self.removal_manager = None
            self.settings_mgr.create_new_session(str(source))
            frame_format = "png"
            suffix = source.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
                image = self.cv2.imread(str(source), self.cv2.IMREAD_COLOR)
                if image is None:
                    raise ApiError("Could not decode image: %s" % source)
                self.cv2.imwrite(os.path.join(self.core.frames_dir, "00000.png"), image)
                height, width = image.shape[:2]
                fps, count, color_space = 24.0, 1, 1
            else:
                container = self.av.open(str(source))
                try:
                    stream = container.streams.video[0]
                    stream.thread_type = "AUTO"
                    width, height = stream.width, stream.height
                    fps = float(stream.average_rate or 24.0)
                    color_space = int(stream.codec_context.colorspace or 1)
                    count = 0
                    for frame in container.decode(stream):
                        array = frame.to_ndarray(format="bgr24")
                        target = os.path.join(self.core.frames_dir, "%05d.%s" % (count, frame_format))
                        if not self.cv2.imwrite(target, array):
                            raise RuntimeError("Failed to write frame %d" % count)
                        count += 1
                finally:
                    container.close()
                if count == 0:
                    raise ApiError("No video frames were decoded")

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
            return self.state()

    def state(self):
        settings = self.settings_mgr.session_settings
        object_ids = sorted({int(p["object_id"]) for p in self.points})
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
            "points": list(self.points),
            "object_ids": object_ids,
            "settings": asdict(settings),
            "segmentation_ready": self.sam_manager is not None,
            "tracking": {
                "frames": tracked_frames,
                "complete": bool(self.core.VideoInfo.total_frames and tracked_frames >= self.core.VideoInfo.total_frames),
            },
        }

    def update_settings(self, values):
        unknown = sorted(set(values) - self.SETTING_KEYS)
        if unknown:
            raise ApiError("Unsupported settings: %s" % ", ".join(unknown))
        for key, value in values.items():
            self.settings_mgr.set_session_setting(key, value)
        self.settings_mgr.save_session_settings()
        return self.state()

    def _ensure_loaded(self):
        if not self.loaded_path:
            raise ApiError("Load media first")

    def _ensure_segmenter(self):
        self._ensure_loaded()
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
                job.status = "failed"
                job.error = "%s\n%s" % (exc, traceback.format_exc())
                job.message = str(exc)
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
        NullProgressDialog.cancelled = True
        return self.jobs[job_id].payload()

    def propagate(self):
        self._ensure_segmenter()

        def run(job):
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
                    job.progress = int((frame_number + 1) * 100 / self.core.VideoInfo.total_frames)
                if first is None:
                    raise RuntimeError("Exporter produced no files")
                return {"path": str(first), "sequence": True, "folder": str(output_root)}
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
                return {"path": str(files[0]), "sequence": True, "folder": str(output_root)}
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
                    job.progress = int((frame_number + 1) * 100 / self.core.VideoInfo.total_frames)
                for packet in stream.encode():
                    container.mux(packet)
            finally:
                container.close()
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Exporter produced no file")
            return {"path": str(output_path), "sequence": False, "folder": str(output_root)}

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
    server_version = "SamosaAE/1.2"

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
                return self._json(self.engine.health())
            if parsed.path == "/api/state":
                return self._json(self.engine.state())
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
            routes = {
                "/api/load": lambda: self.engine.load_media(body["path"]),
                "/api/settings": lambda: self.engine.update_settings(body),
                "/api/points": lambda: self.engine.add_point(body),
                "/api/points/undo": self.engine.undo_point,
                "/api/points/clear": self.engine.clear_points,
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
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or SAMMIE_REPO is required")
    Handler.engine = Engine(Path(args.repo), mock=args.mock)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Samosa AE service listening on http://%s:%d" % (args.host, args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
