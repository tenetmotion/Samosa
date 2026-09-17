"""Isolated SAM 3.1 JSON-lines worker.

The SAM 3 dependency is imported only after a session is requested. This file
has no third-party imports so it can report a useful capability error when the
optional environment has not been installed.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import errno
import importlib
import inspect
import json
import math
import os
import tempfile
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


class WorkerError(RuntimeError):
    def __init__(self, message: str, code: str = "worker_error", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


class CapabilityError(WorkerError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message, "unsupported_capability", details)


def _parent_exists(pid: int | str | None) -> bool:
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.windll.kernel32.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise WorkerError("%s must be a finite number" % name, "invalid_prompt")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkerError("%s must be a finite number" % name, "invalid_prompt") from exc
    if not math.isfinite(value):
        raise WorkerError("%s must be a finite number" % name, "invalid_prompt")
    return value


def _normalized_point(value: Any, name: str = "point") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise WorkerError("%s must contain exactly two coordinates" % name, "invalid_prompt")
    point = [_finite(value[0], "%s.x" % name), _finite(value[1], "%s.y" % name)]
    if not all(0.0 <= item <= 1.0 for item in point):
        raise WorkerError("%s coordinates must be between 0 and 1" % name, "invalid_prompt")
    return point


def _normalized_box(value: Any, name: str = "box") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise WorkerError("%s must contain exactly four coordinates" % name, "invalid_prompt")
    box = [_finite(item, "%s.%s" % (name, axis)) for item, axis in zip(value, ("x1", "y1", "x2", "y2"))]
    if not all(0.0 <= item <= 1.0 for item in box):
        raise WorkerError("%s coordinates must be between 0 and 1" % name, "invalid_prompt")
    if box[2] <= box[0] or box[3] <= box[1]:
        raise WorkerError("%s must have positive width and height" % name, "invalid_prompt")
    return box


def _label(value: Any, name: str = "label") -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"positive", "pos", "include", "foreground", "1"}:
            return 1
        if lowered in {"negative", "neg", "exclude", "background", "0"}:
            return 0
    raise WorkerError("%s must be positive or negative" % name, "invalid_prompt")


def normalize_prompt(prompt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate UI coordinates and translate them to SAM 3's JSON shape."""
    if not isinstance(prompt, Mapping):
        raise WorkerError("prompt must be an object", "invalid_prompt")
    prompt_type = str(prompt.get("type") or prompt.get("prompt_type") or "").strip().lower()
    if prompt_type not in {"point", "box", "text", "example"}:
        raise CapabilityError("unsupported prompt type", {"prompt_type": prompt_type})
    result: dict[str, Any] = {"type": prompt_type}
    if prompt_type == "point":
        points = prompt.get("points")
        if points is None:
            points = [prompt.get("point", prompt.get("coords", prompt.get("coordinates")))]
        if not isinstance(points, (list, tuple)) or not points:
            raise WorkerError("points must contain at least one point", "invalid_prompt")
        result["points"] = [
            _normalized_point(point, "points[%d]" % index)
            for index, point in enumerate(points)
        ]
        labels = prompt.get("labels", prompt.get("point_labels"))
        if labels is None:
            labels = [prompt.get("label", prompt.get("point_label", 1))] * len(result["points"])
        if not isinstance(labels, (list, tuple)) or len(labels) != len(result["points"]):
            raise WorkerError("point labels must match points", "invalid_prompt")
        result["point_labels"] = [
            _label(label, "point_labels[%d]" % index)
            for index, label in enumerate(labels)
        ]
    elif prompt_type == "box":
        xyxy = _normalized_box(prompt.get("box"))
        result["bounding_boxes"] = [[xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]]]
        result["bounding_box_labels"] = [_label(prompt.get("label", 1))]
    elif prompt_type == "text":
        text = prompt.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WorkerError("text prompt must not be empty", "invalid_prompt")
        result["text"] = text.strip()
    else:
        examples = prompt.get("examples")
        if examples is None:
            examples = [{
                "box": prompt.get("box", prompt.get("example_box")),
                "label": prompt.get("label", prompt.get("example_label", 1)),
            }]
        if not isinstance(examples, (list, tuple)) or not examples:
            raise WorkerError("examples must contain at least one box", "invalid_prompt")
        boxes = []
        labels = []
        for index, example in enumerate(examples):
            if not isinstance(example, Mapping):
                raise WorkerError("examples[%d] must be an object" % index, "invalid_prompt")
            xyxy = _normalized_box(example.get("box"), "examples[%d].box" % index)
            boxes.append([xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]])
            labels.append(_label(example.get("label", 1), "examples[%d].label" % index))
        result["bounding_boxes"] = boxes
        result["bounding_box_labels"] = labels
    return result


def _secret_values() -> list[str]:
    values = []
    for key, value in os.environ.items():
        upper = key.upper()
        if not value or len(value) < 8:
            continue
        if upper in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"} or upper.endswith(
            ("_ACCESS_TOKEN", "_AUTH_TOKEN")
        ):
            values.append(value)
    return values


def _redact(text: str) -> str:
    for secret in _secret_values():
        text = text.replace(secret, "[redacted]")
    return text


def _json_safe(value: Any, mask_root: str | os.PathLike[str] | None = None, prefix: str = "result") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return _redact(value) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return _redact(str(value))
    if isinstance(value, bytes):
        return {"encoding": "base64", "dtype": "uint8", "shape": [len(value)], "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {_redact(str(key)): _json_safe(item, mask_root, "%s_%s" % (prefix, key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, mask_root, "%s_%d" % (prefix, index)) for index, item in enumerate(value)]
    candidate = value
    try:
        if hasattr(candidate, "detach"):
            candidate = candidate.detach()
        if hasattr(candidate, "cpu"):
            candidate = candidate.cpu()
        if hasattr(candidate, "numpy"):
            candidate = candidate.numpy()
        if hasattr(candidate, "shape") and hasattr(candidate, "tobytes"):
            size = 1
            for dimension in candidate.shape:
                size *= int(dimension)
            if size > 4096:
                dtype = str(getattr(candidate, "dtype", "unknown"))
                if mask_root:
                    root = Path(mask_root)
                    root.mkdir(parents=True, exist_ok=True)
                    safe_prefix = "".join(character if character.isalnum() or character in "._-" else "_" for character in prefix)[:80]
                    path = root / (safe_prefix + "_" + os.urandom(8).hex() + ".bin")
                    path.write_bytes(candidate.tobytes())
                    return {"encoding": "raw", "dtype": dtype, "shape": [int(item) for item in candidate.shape], "path": str(path)}
                return {
                    "encoding": "base64",
                    "dtype": dtype,
                    "shape": [int(item) for item in candidate.shape],
                    "data": base64.b64encode(candidate.tobytes()).decode("ascii"),
                }
        if hasattr(candidate, "tolist"):
            return _json_safe(candidate.tolist(), mask_root, prefix)
    except Exception:
        return {"type": type(value).__name__, "serializable": False}
    return str(value)


def _invoke(func: Callable[..., Any], request: Mapping[str, Any]) -> Any:
    """Pass only supported kwargs when an upstream revision exposes kwargs."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(request)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return func(**dict(request))
    if all(parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY) for parameter in parameters.values()):
        accepted = {key: value for key, value in request.items() if key in parameters}
        if accepted:
            return func(**accepted)
    return func(request)


class Sam3Worker:
    """Protocol and lifecycle adapter around the optional official predictor."""

    PROMPTS = ("point", "box", "text", "example")

    def __init__(self, predictor_factory: Callable[[Mapping[str, Any]], Any] | None = None, parent_pid: int | None = None):
        self.predictor_factory = predictor_factory
        self.parent_pid = parent_pid
        self.predictor: Any = None
        self.sessions: dict[str, dict[str, Any]] = {}
        self.active_cancellations: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.predictor_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.mask_root: str | None = None

    def handle(self, command: str, payload: Mapping[str, Any] | None = None, emit: Callable[[Any], None] | None = None) -> Any:
        payload = dict(payload or {})
        if command == "health":
            return self.health()
        if command == "start_session":
            return self.start_session(payload, emit)
        if command == "add_prompt":
            return self.add_prompt(payload)
        if command == "propagate":
            return self.propagate(payload, emit or (lambda value: None), None)
        if command == "remove_object":
            return self.remove_object(payload)
        if command == "reset_session":
            return self.reset_session(payload)
        if command == "close_session":
            return self.close_session(payload)
        if command == "cancel":
            target = str(payload.get("request_id") or "")
            with self.lock:
                event = self.active_cancellations.get(target)
            if event is None:
                return {"request_id": target, "cancelled": False}
            event.set()
            return {"request_id": target, "cancelled": True}
        if command == "shutdown":
            self.shutdown()
            return {"shutdown": True}
        raise WorkerError("unknown worker command", "unknown_command")

    def health(self) -> dict[str, Any]:
        available, error = self._sam3_installation_state()
        return {
            "ok": True,
            "worker_pid": os.getpid(),
            "sam3_available": available,
            "model_loaded": self.predictor is not None,
            "sessions": len(self.sessions),
            "capabilities": {"prompts": list(self.PROMPTS), "commands": ["health", "start_session", "add_prompt", "propagate", "remove_object", "reset_session", "close_session", "shutdown"]},
            "unavailable_reason": error,
        }

    def start_session(
        self,
        payload: Mapping[str, Any],
        emit: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise WorkerError("session_id is required", "invalid_session")
        video_path = payload.get("video_path", payload.get("resource_path"))
        if not isinstance(video_path, str) or not video_path:
            raise WorkerError("video_path is required", "invalid_session")
        config = self._config(payload)
        emit = emit or (lambda value: None)
        started = time.monotonic()
        phase = {"name": "loading_model"}
        heartbeat_stop = threading.Event()

        def report(message: str) -> None:
            emit({
                "phase": phase["name"],
                "message": message,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            })

        def heartbeat() -> None:
            while not heartbeat_stop.wait(5.0):
                report("SAM 3.1 is still working")

        report("Loading the SAM 3.1 model")
        heartbeat_thread = threading.Thread(
            target=heartbeat,
            daemon=True,
            name="samosa-sam3-startup-heartbeat",
        )
        heartbeat_thread.start()
        try:
            self._ensure_predictor(config)
            phase["name"] = "opening_video"
            report("Preparing the video session")
            request = {
                "type": "start_session",
                "session_id": session_id,
                "resource_path": video_path,
                "video_path": video_path,
            }
            result = self._request(request)
        except WorkerError:
            raise
        except Exception as exc:
            raise self._upstream_error(exc) from exc
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=0.2)
        with self.lock:
            self.sessions[session_id] = {"video_path": video_path}
        phase["name"] = "ready"
        report("SAM 3.1 session ready")
        return {"session_id": session_id, "result": _json_safe(result, self.mask_root, "start_session")}

    def add_prompt(self, payload: Mapping[str, Any]) -> Any:
        session_id = self._session_id(payload)
        frame_index = self._frame_index(payload)
        prompt = normalize_prompt(payload.get("prompt", payload))
        request = {
            "type": "add_prompt",
            "prompt_type": prompt["type"],
            "session_id": session_id,
            "frame_index": frame_index,
            "obj_id": payload.get("obj_id"),
            **{key: value for key, value in prompt.items() if key != "type"},
        }
        try:
            result = self._add_prompt(request, prompt)
        except WorkerError:
            raise
        except Exception as exc:
            raise self._upstream_error(exc, prompt["type"]) from exc
        return _json_safe(result, self.mask_root, "prompt")

    def propagate(self, payload: Mapping[str, Any], emit: Callable[[Any], None], cancel_event: threading.Event | None) -> dict[str, Any]:
        session_id = self._session_id(payload)
        request_id = str(payload.get("request_id") or "")
        if cancel_event is None:
            cancel_event = threading.Event()
        with self.lock:
            if request_id:
                self.active_cancellations[request_id] = cancel_event
        request = {"type": "propagate_in_video", "session_id": session_id}
        for key in ("start_frame_index", "end_frame_index", "max_frame_num_to_track", "reverse"):
            if key in payload:
                request[key] = payload[key]
        if "start_frame_index" in request:
            request["start_frame_idx"] = request["start_frame_index"]
        if "end_frame_index" in request:
            request["end_frame_idx"] = request["end_frame_index"]
        count = 0
        try:
            with self.predictor_lock:
                stream = self._stream(request)
                with contextlib.redirect_stdout(sys.stderr):
                    for item in stream:
                        if cancel_event.is_set():
                            return {"cancelled": True, "events": count}
                        emit(_json_safe(item, self.mask_root, "propagate_%d" % count))
                        count += 1
            return {"cancelled": cancel_event.is_set(), "events": count}
        except Exception as exc:
            raise self._upstream_error(exc, "propagate") from exc
        finally:
            if request_id:
                with self.lock:
                    self.active_cancellations.pop(request_id, None)

    def remove_object(self, payload: Mapping[str, Any]) -> Any:
        request = {"type": "remove_object", "session_id": self._session_id(payload), "obj_id": payload.get("obj_id")}
        return _json_safe(self._request(request), self.mask_root, "remove_object")

    def reset_session(self, payload: Mapping[str, Any]) -> Any:
        request = {"type": "reset_session", "session_id": self._session_id(payload)}
        return _json_safe(self._request(request), self.mask_root, "reset_session")

    def close_session(self, payload: Mapping[str, Any]) -> Any:
        session_id = self._session_id(payload)
        result = _json_safe(self._request({"type": "close_session", "session_id": session_id}), self.mask_root, "close_session")
        with self.lock:
            self.sessions.pop(session_id, None)
        return result

    def shutdown(self) -> None:
        with self.lock:
            sessions = list(self.sessions)
            self.stop_event.set()
            for event in self.active_cancellations.values():
                event.set()
        for session_id in sessions:
            try:
                self._request({"type": "close_session", "session_id": session_id})
            except Exception:
                pass
        with self.lock:
            self.sessions.clear()
            self.predictor = None

    def run(self, stdin: Any = None, stdout: Any = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        write_lock = threading.Lock()

        def respond(message: Mapping[str, Any]) -> None:
            with write_lock:
                stdout.write(json.dumps(message, separators=(",", ":"), ensure_ascii=True) + "\n")
                stdout.flush()

        if self.parent_pid:
            threading.Thread(target=self._parent_watchdog, daemon=True, name="samosa-sam3-parent-watchdog").start()
        for line in stdin:
            if self.stop_event.is_set():
                break
            request: Any = {}
            try:
                request = json.loads(line)
                if not isinstance(request, Mapping) or not isinstance(request.get("id"), str):
                    raise WorkerError("request must contain a string id", "invalid_request")
                request_id = request["id"]
                command = str(request.get("command") or "")
                payload = request.get("payload") or {}
                if not isinstance(payload, Mapping):
                    raise WorkerError("payload must be an object", "invalid_request")
            except (ValueError, TypeError, WorkerError) as exc:
                error = self._error_payload(exc)
                respond({"id": request.get("id", "") if isinstance(request, Mapping) else "", "ok": False, "error": error})
                continue

            if command == "propagate":
                cancel_event = threading.Event()
                with self.lock:
                    self.active_cancellations[request_id] = cancel_event
                thread = threading.Thread(
                    target=self._dispatch,
                    args=(request_id, command, payload, respond, cancel_event),
                    daemon=True,
                )
                thread.start()
            else:
                # CUDA and Triton initialization can stall when first entered from
                # a background thread on Windows. Keep ordinary inference on the
                # process main thread; propagation alone needs a worker thread so
                # the protocol can continue accepting cancellation requests.
                self._dispatch(request_id, command, payload, respond, None)

    def _dispatch(self, request_id: str, command: str, payload: Mapping[str, Any], respond: Callable[[Mapping[str, Any]], None], cancel_event: threading.Event | None) -> None:
        events = []

        def emit(value: Any) -> None:
            respond({"id": request_id, "event": "result", "result": value})

        try:
            if command == "propagate":
                payload = dict(payload)
                payload["request_id"] = request_id
                result = self.propagate(payload, emit, cancel_event)
            else:
                result = self.handle(command, payload, emit)
            respond({"id": request_id, "ok": True, "result": _json_safe(result)})
        except Exception as exc:
            respond({"id": request_id, "ok": False, "error": self._error_payload(exc)})

    def _session_id(self, payload: Mapping[str, Any]) -> str:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise WorkerError("session_id is required", "invalid_session")
        with self.lock:
            if session_id not in self.sessions:
                raise WorkerError("session does not exist", "invalid_session")
        return session_id

    @staticmethod
    def _frame_index(payload: Mapping[str, Any]) -> int:
        value = payload.get("frame_index", payload.get("frame_idx", 0))
        if isinstance(value, bool):
            raise WorkerError("frame_index must be a non-negative integer", "invalid_prompt")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkerError("frame_index must be a non-negative integer", "invalid_prompt") from exc
        if value < 0:
            raise WorkerError("frame_index must be a non-negative integer", "invalid_prompt")
        return value

    def _config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        config = {
            "repo": payload.get("repo_path") or os.environ.get("SAMOSA_SAM3_REPO") or os.environ.get("SAM3_REPO"),
            "checkpoint": payload.get("checkpoint") or os.environ.get("SAMOSA_SAM3_CHECKPOINT") or os.environ.get("SAM3_CHECKPOINT"),
            "cache": payload.get("cache_dir") or os.environ.get("SAMOSA_SAM3_CACHE") or os.environ.get("HF_HOME"),
            "use_fa3": payload.get("use_fa3", os.environ.get("SAMOSA_SAM3_USE_FA3", "false").lower() in {"1", "true", "yes"}),
            "mask_root": payload.get("mask_root") or os.environ.get("SAMOSA_SAM3_MASK_ROOT") or os.environ.get("SAM3_MASK_ROOT") or str(Path(tempfile.gettempdir()) / "samosa-sam3-masks"),
        }
        if "hf_token" in payload or "token" in payload:
            raise WorkerError(
                "the inference worker does not accept credentials", "credential_error"
            )
        self.mask_root = os.fspath(config["mask_root"])
        return config

    def _ensure_predictor(self, config: Mapping[str, Any]) -> None:
        with self.predictor_lock:
            if self.predictor is not None:
                return
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    predictor = (
                        self.predictor_factory(config)
                        if self.predictor_factory
                        else self._build_predictor(config)
                    )
                    self.predictor = self._adapt_predictor(predictor)
            except WorkerError:
                raise
            except Exception as exc:
                raise self._upstream_error(exc) from exc

    @staticmethod
    def _adapt_predictor(predictor: Any) -> Any:
        """Normalize optional session kwargs across pinned upstream revisions."""
        model = getattr(predictor, "model", None)
        init_state = getattr(model, "init_state", None)
        if not callable(init_state) or getattr(init_state, "_samosa_compatible", False):
            return predictor
        try:
            signature = inspect.signature(init_state)
        except (TypeError, ValueError):
            return predictor
        parameters = signature.parameters
        if any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return predictor
        optional = {
            "async_loading_frames",
            "offload_state_to_cpu",
            "offload_video_to_cpu",
            "video_loader_type",
        }

        def compatible_init_state(*args: Any, **kwargs: Any) -> Any:
            unsupported = set(kwargs) - set(parameters)
            if unsupported - optional:
                return init_state(*args, **kwargs)
            accepted = {key: value for key, value in kwargs.items() if key in parameters}
            return init_state(*args, **accepted)

        compatible_init_state._samosa_compatible = True
        model.init_state = compatible_init_state
        return predictor

    def _sam3_available(self) -> tuple[bool, str | None]:
        try:
            repo = os.environ.get("SAMOSA_SAM3_REPO") or os.environ.get("SAM3_REPO")
            if repo and os.fspath(repo) not in sys.path:
                sys.path.insert(0, os.fspath(repo))
            with contextlib.redirect_stdout(sys.stderr):
                importlib.import_module("sam3.model_builder")
            return True, None
        except Exception as exc:
            return False, _redact(type(exc).__name__ + ": " + str(exc))

    @staticmethod
    def _sam3_installation_state() -> tuple[bool, str | None]:
        """Check worker prerequisites without importing torch or SAM 3.1."""
        repo = os.environ.get("SAMOSA_SAM3_REPO") or os.environ.get("SAM3_REPO")
        checkpoint = (
            os.environ.get("SAMOSA_SAM3_CHECKPOINT")
            or os.environ.get("SAM3_CHECKPOINT")
        )
        if not repo or not (Path(repo) / "sam3" / "model_builder.py").is_file():
            return False, "SAM 3.1 source is not installed"
        if checkpoint and not Path(checkpoint).is_file():
            return False, "SAM 3.1 checkpoint is not installed"
        return True, None

    @staticmethod
    def _build_predictor(config: Mapping[str, Any]) -> Any:
        repo = config.get("repo")
        if repo:
            repo_path = os.fspath(repo)
            if repo_path not in sys.path:
                sys.path.insert(0, repo_path)
        cache = config.get("cache")
        if cache:
            os.environ.setdefault("HF_HOME", os.fspath(cache))
        try:
            module = importlib.import_module("sam3.model_builder")
            builder = getattr(module, "build_sam3_multiplex_video_predictor")
        except (ImportError, AttributeError) as exc:
            raise CapabilityError("SAM 3.1 is not installed in the isolated environment", {"install": "sam3.model_builder.build_sam3_multiplex_video_predictor"}) from exc
        kwargs: dict[str, Any] = {"use_fa3": bool(config.get("use_fa3", False))}
        checkpoint = config.get("checkpoint")
        if checkpoint:
            kwargs["checkpoint_path"] = os.fspath(checkpoint)
        try:
            return _invoke(builder, kwargs)
        except TypeError as exc:
            # A revision may name the checkpoint argument differently.
            for name in ("checkpoint", "ckpt_path"):
                if checkpoint:
                    try:
                        alternate = dict(kwargs)
                        alternate.pop("checkpoint_path", None)
                        alternate[name] = os.fspath(checkpoint)
                        return _invoke(builder, alternate)
                    except TypeError:
                        continue
            raise CapabilityError("installed SAM 3.1 builder does not support the configured options") from exc

    def _request(self, request: Mapping[str, Any]) -> Any:
        with self.predictor_lock:
            if self.predictor is None:
                raise WorkerError("SAM 3.1 predictor is not loaded", "model_not_loaded")
            handle = getattr(self.predictor, "handle_request", None)
            if callable(handle):
                with contextlib.redirect_stdout(sys.stderr):
                    return _invoke(handle, request)
            method = getattr(self.predictor, str(request.get("type") or ""), None)
            if callable(method):
                with contextlib.redirect_stdout(sys.stderr):
                    return _invoke(method, request)
            raise CapabilityError("installed SAM 3.1 predictor does not expose %s" % request.get("type"))

    def _add_prompt(self, request: Mapping[str, Any], prompt: Mapping[str, Any]) -> Any:
        """Prefer the official add_prompt API, then fall back to request dispatch."""
        with self.predictor_lock:
            method = getattr(self.predictor, "add_prompt", None)
            if callable(method):
                prompt_type = str(prompt["type"])
                arguments: dict[str, Any] = {
                    "session_id": request["session_id"],
                    "frame_idx": request["frame_index"],
                    "obj_id": request.get("obj_id"),
                }
                if prompt_type == "point":
                    arguments.update(points=prompt["points"], point_labels=prompt["point_labels"], rel_coordinates=True)
                elif prompt_type in {"box", "example"}:
                    arguments.update(bounding_boxes=prompt["bounding_boxes"], bounding_box_labels=prompt["bounding_box_labels"], rel_coordinates=True)
                elif prompt_type == "text":
                    arguments["text"] = prompt["text"]
                try:
                    with contextlib.redirect_stdout(sys.stderr):
                        return _invoke(method, arguments)
                except TypeError:
                    arguments["frame_index"] = arguments.pop("frame_idx")
                    try:
                        with contextlib.redirect_stdout(sys.stderr):
                            return _invoke(method, arguments)
                    except TypeError as exc:
                        if prompt_type == "example":
                            raise CapabilityError("installed SAM 3.1 revision does not support example prompts") from exc
                        raise
            return self._request(request)

    def _stream(self, request: Mapping[str, Any]) -> Iterable[Any]:
        if self.predictor is None:
            raise WorkerError("SAM 3.1 predictor is not loaded", "model_not_loaded")
        handle = getattr(self.predictor, "handle_stream_request", None)
        if callable(handle):
            stream = _invoke(handle, request)
        else:
            method = getattr(self.predictor, "propagate_in_video", None)
            if not callable(method):
                raise CapabilityError("installed SAM 3.1 predictor does not expose propagation")
            stream = _invoke(method, request)
        if isinstance(stream, Mapping) or isinstance(stream, (str, bytes)):
            return (stream,)
        return iter(stream)

    @staticmethod
    def _upstream_error(exc: Exception, capability: str | None = None) -> WorkerError:
        message = _redact(str(exc) or type(exc).__name__)
        return WorkerError("SAM 3.1 operation failed: " + message, "upstream_error")

    @staticmethod
    def _error_payload(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, WorkerError):
            result = {"code": exc.code, "message": _redact(str(exc))}
            if exc.details is not None:
                result["details"] = _json_safe(exc.details)
            return result
        return {"code": "worker_error", "message": _redact(str(exc) or type(exc).__name__)}

    def _parent_watchdog(self) -> None:
        while not self.stop_event.wait(0.5):
            if not _parent_exists(self.parent_pid):
                os._exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Samosa isolated SAM 3.1 worker")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--cache", default=None)
    parser.add_argument("--use-fa3", action="store_true")
    parser.add_argument("--parent-pid", type=int, default=None)
    args = parser.parse_args(argv)
    if args.repo:
        os.environ["SAMOSA_SAM3_REPO"] = args.repo
    if args.checkpoint:
        os.environ["SAMOSA_SAM3_CHECKPOINT"] = args.checkpoint
    if args.cache:
        os.environ["SAMOSA_SAM3_CACHE"] = args.cache
    if args.use_fa3:
        os.environ["SAMOSA_SAM3_USE_FA3"] = "true"
    Sam3Worker(parent_pid=args.parent_pid or os.environ.get("SAM3_PARENT_PID")).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CapabilityError", "Sam3Worker", "WorkerError", "normalize_prompt"]
