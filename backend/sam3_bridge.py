"""Thread-safe JSON-lines bridge for the isolated SAM 3.1 worker."""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class Sam3BridgeError(RuntimeError):
    """An RPC, worker, startup, or transport error."""

    def __init__(self, message: str, code: str = "bridge_error", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": str(self)}
        if self.details is not None:
            result["details"] = self.details
        return result


@dataclass
class _PendingRequest:
    messages: "queue.Queue[dict[str, Any]]"
    on_event: Callable[[dict[str, Any]], None] | None = None
    last_event: dict[str, Any] | None = None


class Sam3Bridge:
    """Own one isolated worker process and correlate concurrent JSON RPC calls."""

    def __init__(
        self,
        command: Sequence[str] | str | None = None,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
        startup_timeout: float = 15.0,
        request_timeout: float = 120.0,
    ) -> None:
        if command is None:
            command = [sys.executable, "-u", str(Path(__file__).with_name("sam3_worker.py"))]
        elif isinstance(command, str):
            command = shlex.split(command, posix=os.name != "nt")
        if not command:
            raise ValueError("command must not be empty")
        self.command = [str(part) for part in command]
        self.env = dict(env or {})
        self.cwd = os.fspath(cwd) if cwd is not None else None
        self.startup_timeout = float(startup_timeout)
        self.request_timeout = float(request_timeout)
        if self.startup_timeout <= 0 or self.request_timeout <= 0:
            raise ValueError("timeouts must be positive")

        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: "deque[str]" = deque(maxlen=40)
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._pending: dict[str, _PendingRequest] = {}
        self._next_id = 0
        self._closed = False
        self._atexit_registered = False

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            return self._process.pid if self._process is not None else None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._process is not None and self._process.poll() is None

    def start(self) -> dict[str, Any]:
        with self._state_lock:
            if self._closed:
                raise Sam3BridgeError("bridge is closed", "bridge_closed")
            if self.is_running:
                return self.call("health", timeout=self.startup_timeout)

            child_env = os.environ.copy()
            child_env.update(self.env)
            child_env["SAM3_PARENT_PID"] = str(os.getpid())
            # The bridge never puts environment contents into an exception or log.
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                self._process = subprocess.Popen(
                    self.command,
                    cwd=self.cwd,
                    env=child_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                self._process = None
                raise Sam3BridgeError("could not start SAM 3 worker", "startup_failed") from exc

            self._reader_thread = threading.Thread(
                target=self._read_responses,
                name="samosa-sam3-rpc-reader",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name="samosa-sam3-stderr-reader",
                daemon=True,
            )
            self._stderr_thread.start()
            if not self._atexit_registered:
                atexit.register(self.close)
                self._atexit_registered = True

        try:
            return self._call("health", {}, timeout=self.startup_timeout)
        except Sam3BridgeError as exc:
            if exc.code in {"timeout", "startup_timeout", "bridge_crashed", "worker_protocol"}:
                self._terminate()
            if exc.code == "timeout":
                exc.code = "startup_timeout"
            raise

    def call(
        self,
        command: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Send one request and optionally receive stream events through ``on_event``."""
        if command == "health" and not self.is_running:
            return self.start()
        self._ensure_started()
        return self._call(command, dict(payload or {}), timeout=timeout, on_event=on_event, request_id=request_id)

    def propagate(
        self,
        payload: Mapping[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        """Run propagation, optionally forwarding a local cancellation event."""
        chosen_id = request_id or self._new_request_id()
        watcher = None
        watcher_stop = threading.Event()
        if cancel_event is not None:
            watcher = threading.Thread(
                target=self._watch_cancel,
                args=(cancel_event, watcher_stop, chosen_id),
                name="samosa-sam3-cancel-watcher",
                daemon=True,
            )
            watcher.start()
        try:
            return self.call(
                "propagate",
                payload,
                timeout=timeout,
                on_event=on_event,
                request_id=chosen_id,
            )
        finally:
            if watcher is not None:
                watcher_stop.set()

    def cancel(self, request_id: str, *, timeout: float | None = None) -> Any:
        """Request cancellation of a currently streaming request."""
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        return self.call("cancel", {"request_id": request_id}, timeout=timeout)

    def close(self, *, timeout: float = 3.0) -> None:
        with self._state_lock:
            if self._process is None:
                self._closed = True
                return
            process = self._process
        try:
            if process.poll() is None:
                try:
                    self._call("shutdown", {}, timeout=timeout)
                except Sam3BridgeError:
                    pass
        finally:
            self._terminate()
            with self._state_lock:
                self._closed = True

    def __enter__(self) -> "Sam3Bridge":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if not self.is_running:
            self.start()

    def _new_request_id(self) -> str:
        with self._state_lock:
            self._next_id += 1
            return "%s-%d" % (uuid.uuid4().hex, self._next_id)

    def _call(
        self,
        command: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        request_id: str | None = None,
    ) -> Any:
        request_id = request_id or self._new_request_id()
        pending = _PendingRequest(queue.Queue(), on_event)
        with self._state_lock:
            if request_id in self._pending:
                raise Sam3BridgeError("request ID is already active", "duplicate_request_id")
            self._pending[request_id] = pending
        try:
            self._send({"id": request_id, "command": command, "payload": dict(payload)})
            deadline = time.monotonic() + (self.request_timeout if timeout is None else float(timeout))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._timeout_error(pending)
                try:
                    message = pending.messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise self._timeout_error(pending) from exc
                if message.get("event") is not None:
                    pending.last_event = message
                    if on_event is not None:
                        try:
                            on_event(message)
                        except Exception:
                            # Consumer callbacks must not corrupt the transport.
                            pass
                    continue
                if message.get("ok"):
                    return message.get("result")
                error = message.get("error")
                if isinstance(error, Mapping):
                    raise Sam3BridgeError(
                        str(error.get("message") or "SAM 3 worker request failed"),
                        str(error.get("code") or "worker_error"),
                        error.get("details"),
                    )
                raise Sam3BridgeError("SAM 3 worker request failed", "worker_error")
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def _send(self, message: Mapping[str, Any]) -> None:
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise Sam3BridgeError("SAM 3 worker is not running", "bridge_crashed")
            try:
                process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise Sam3BridgeError("SAM 3 worker pipe closed", "bridge_crashed") from exc

    def _read_responses(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (TypeError, ValueError):
                    self._fail_pending(Sam3BridgeError("worker returned invalid JSON", "worker_protocol"))
                    continue
                if not isinstance(message, dict) or not isinstance(message.get("id"), str):
                    self._fail_pending(Sam3BridgeError("worker returned an invalid response", "worker_protocol"))
                    continue
                with self._state_lock:
                    pending = self._pending.get(message["id"])
                if pending is not None:
                    pending.messages.put(message)
        finally:
            self._fail_pending(
                Sam3BridgeError("SAM 3 worker exited unexpectedly", "bridge_crashed")
            )

    @staticmethod
    def _redact_diagnostic(value: str) -> str:
        return re.sub(r"hf_[A-Za-z0-9_-]+", "[redacted]", str(value or ""))

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            safe = self._redact_diagnostic(line.strip())
            if safe:
                self._stderr_lines.append(safe)

    def _timeout_error(self, pending: _PendingRequest) -> Sam3BridgeError:
        result = pending.last_event.get("result") if pending.last_event else None
        phase = result.get("phase") if isinstance(result, Mapping) else None
        message = "SAM 3 worker request timed out"
        if phase:
            message += " during " + str(phase).replace("_", " ")
        details: dict[str, Any] = {}
        if phase:
            details["phase"] = phase
        if self._stderr_lines:
            details["worker_stderr"] = list(self._stderr_lines)[-8:]
        return Sam3BridgeError(message, "timeout", details or None)

    def _fail_pending(self, error: Sam3BridgeError) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
        for item in pending:
            item.messages.put({"id": "", "ok": False, "error": error.as_dict()})

    def _watch_cancel(self, cancel_event: threading.Event, watcher_stop: threading.Event, request_id: str) -> None:
        while not watcher_stop.is_set():
            if cancel_event.wait(0.05):
                try:
                    self.cancel(request_id, timeout=min(self.request_timeout, 5.0))
                except Sam3BridgeError:
                    pass
                return

    def _terminate(self) -> None:
        with self._state_lock:
            process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        reader = self._reader_thread
        if (
            reader is not None
            and reader is not threading.current_thread()
            and reader.is_alive()
        ):
            reader.join(timeout=1.0)
        stderr_reader = self._stderr_thread
        if (
            stderr_reader is not None
            and stderr_reader is not threading.current_thread()
            and stderr_reader.is_alive()
        ):
            stderr_reader.join(timeout=1.0)
        with self._state_lock:
            self._process = None
            self._reader_thread = None
            self._stderr_thread = None
        self._fail_pending(Sam3BridgeError("SAM 3 worker terminated", "bridge_closed"))


__all__ = ["Sam3Bridge", "Sam3BridgeError"]
