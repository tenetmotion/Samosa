"""Process boundary for the optional CorridorKey 2.0 runtime."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class CorridorKeyError(RuntimeError):
    pass


class CorridorKeyRunner:
    """Launch the unmodified upstream CLI and translate its output to Samosa jobs."""

    _WINDOW_RE = re.compile(r"\bwindow\s+(\d+)\s*/\s*(\d+)\b", re.IGNORECASE)

    def __init__(self, python_path, repo_path):
        self.python_path = Path(python_path).resolve()
        self.repo_path = Path(repo_path).resolve()

    def validate(self):
        required = (
            self.python_path,
            self.repo_path / "infer.py",
            self.repo_path / "weights" / "corridorkey_v2.safetensors",
            self.repo_path / "weights" / "foundation_stats.safetensors",
            self.repo_path / "vfms" / "rvm" / "pretrain_rvm_small16_256_204031069.npz",
            self.repo_path / "vfms" / "moge" / "model.pt",
            self.repo_path / "vfms" / "cradio" / "C-RADIOv4-SO400M",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise CorridorKeyError("CorridorKey runtime is incomplete: " + ", ".join(missing))

    def build_command(
        self,
        *,
        input_dir,
        output_dir,
        temp_dir,
        fps,
        hint_mode="tracked",
        hint_path=None,
        hint_quality=0.75,
        temporal="auto",
        model_scale=1.0,
        despill=0.5,
    ):
        if hint_mode not in ("tracked", "first", "automatic"):
            raise CorridorKeyError("Unsupported CorridorKey hint mode: %s" % hint_mode)
        if temporal not in ("auto", "low_vram", "frame"):
            raise CorridorKeyError("Unsupported CorridorKey temporal mode: %s" % temporal)
        if hint_mode != "automatic" and not hint_path:
            raise CorridorKeyError("CorridorKey hint input is missing")

        command = [
            str(self.python_path), "-u", str(self.repo_path / "infer.py"),
            "--input", str(Path(input_dir).resolve()),
            "--output_dir", str(Path(output_dir).resolve()),
            "--temp_dir", str(Path(temp_dir).resolve()),
            "--frame_dir_fps", str(float(fps)),
            "--outputs", "alpha", "cutout",
            "--output_format", "exr",
            "--exr_dtype", "half",
            "--cutout_linear",
            "--hint_quality", str(max(0.0, min(1.0, float(hint_quality)))),
            "--model_scale", str(max(0.25, min(1.0, float(model_scale)))),
            "--despill_strength", str(max(0.0, min(1.0, float(despill)))),
        ]
        if hint_mode == "tracked":
            command.extend(("--hint_video", str(Path(hint_path).resolve())))
        elif hint_mode == "first":
            command.extend(("--hint_first_frame", str(Path(hint_path).resolve())))

        if temporal == "low_vram":
            command.extend(("--low_vram", "--hann_chunk", "8", "--hann_stride", "4"))
        elif temporal == "frame":
            command.extend(("--hann_chunk", "1", "--hann_stride", "1", "--no_carry_hint"))
        return command

    @classmethod
    def progress_from_line(cls, line, current=10):
        lowered = str(line or "").lower()
        match = cls._WINDOW_RE.search(lowered)
        if match and int(match.group(2)) > 0:
            return max(current, 50 + int(35 * int(match.group(1)) / int(match.group(2))))
        phases = (
            ("loading corridorkey", 12),
            ("auto vram", 16),
            ("rvm continuous prepass", 22),
            ("frozen vfm prepass", 36),
            ("hann stage1", 50),
            ("wrote ", 92),
        )
        for marker, value in phases:
            if marker in lowered:
                return max(current, value)
        return current

    def run(self, command, job, env=None):
        self.validate()
        process_env = os.environ.copy()
        process_env.update(env or {})
        process_env.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=str(self.repo_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=process_env,
            creationflags=creationflags,
        )
        job.process = process
        tail = []
        try:
            for raw_line in iter(process.stdout.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-12]
                job.progress = self.progress_from_line(line, job.progress)
                job.message = "CorridorKey: " + line[:180]
                if job.cancelled:
                    process.terminate()
                    break
            return_code = process.wait()
        finally:
            job.process = None
            if process.stdout:
                process.stdout.close()
        if job.cancelled:
            return
        if return_code != 0:
            detail = tail[-1] if tail else "no diagnostic output"
            raise CorridorKeyError(
                "CorridorKey exited with code %d: %s" % (return_code, detail)
            )

    @staticmethod
    def discover_frames(output_dir, kind, expected_count):
        root = Path(output_dir)
        candidates = sorted(root.glob("*_%s_exr/%s_*.exr" % (kind, kind)))
        if len(candidates) != int(expected_count):
            raise CorridorKeyError(
                "CorridorKey produced %d %s frames; expected %d"
                % (len(candidates), kind, expected_count)
            )
        return candidates


__all__ = ["CorridorKeyError", "CorridorKeyRunner"]
