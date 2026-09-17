import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

from backend import service


class FakeSettings:
    def __init__(self):
        self.values = {"sam_model": "SAM3.1"}
        self.saved_points = []

    def get_session_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_session_setting(self, key, value):
        self.values[key] = value

    def save_points(self, points):
        self.saved_points = list(points)

    def save_session_settings(self):
        return None


class FakeCv2:
    INTER_NEAREST = 0
    IMREAD_COLOR = 1
    IMWRITE_JPEG_QUALITY = 2

    @staticmethod
    def imread(path, flags=None):
        if not Path(path).is_file():
            return None
        return np.zeros((4, 6, 3), dtype=np.uint8)

    @staticmethod
    def imwrite(path, array, params=None):
        Path(path).write_bytes(array.tobytes())
        return True

    @staticmethod
    def resize(array, size, interpolation=None):
        return np.resize(array, (size[1], size[0]))


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.propagation_events = []

    def call(self, command, payload, **kwargs):
        self.calls.append((command, payload))
        if command != "add_prompt":
            return {"ok": True}
        prompt_type = payload["prompt"]["type"]
        if prompt_type == "text":
            object_ids = [2, 3]
        else:
            object_ids = [int(payload.get("obj_id") or 0)]
        masks = []
        for index in range(len(object_ids)):
            mask = np.zeros((4, 6), dtype=bool)
            mask[index:index + 2, index:index + 3] = True
            masks.append(mask.tolist())
        return {
            "frame_index": payload["frame_index"],
            "outputs": {
                "out_obj_ids": object_ids,
                "out_binary_masks": masks,
            },
        }

    def propagate(self, payload, on_event, cancel_event, timeout):
        for frame in range(3):
            mask = np.zeros((4, 6), dtype=bool)
            mask[:, frame:frame + 1] = True
            on_event({
                "result": {
                    "frame_index": frame,
                    "outputs": {
                        "out_obj_ids": [0],
                        "out_binary_masks": [mask.tolist()],
                    },
                }
            })
        return {"cancelled": False, "events": 3}

    def close(self):
        return None


@unittest.skipUnless(np is not None, "NumPy is required for mask adapter tests")
class Sam3ServiceTests(unittest.TestCase):
    def make_engine(self, root):
        engine = object.__new__(service.Engine)
        engine.loaded_path = "clip.mov"
        engine.mock = True
        engine.lock = __import__("threading").RLock()
        engine.jobs = {}
        engine.settings_mgr = FakeSettings()
        engine.np = np
        engine.cv2 = FakeCv2()
        engine.core = SimpleNamespace(
            frames_dir=str(root / "frames"),
            mask_dir=str(root / "masks"),
            temp_dir=str(root),
            VideoInfo=SimpleNamespace(width=6, height=4, total_frames=3, fps=24),
        )
        Path(engine.core.frames_dir).mkdir()
        Path(engine.core.mask_dir).mkdir()
        engine.sam_manager = None
        engine.sam3_bridge = FakeBridge()
        engine.sam3_session_id = "session"
        engine.sam3_transport_dir = root / "transport"
        engine.sam3_frames_dir = root / "sam3_frames"
        engine.sam3_prompts = []
        engine.sam3_object_ids = set()
        engine.sam3_install_job_id = None
        engine.matting_manager = None
        engine.removal_manager = None
        engine.source_start_time = 0
        engine.source_end_time = 3 / 24
        engine.source_start_frame = 0
        engine.source_end_frame_exclusive = 3
        engine.trimmed = False
        engine.state = lambda: {
            "points": list(engine.points),
            "object_ids": sorted({point["object_id"] for point in engine.points}),
        }
        engine.points = []
        return engine

    def test_prepares_complete_jpeg_sequence_for_official_video_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            for index in range(3):
                (Path(engine.core.frames_dir) / ("%05d.png" % index)).write_bytes(b"png")
            resource_path = Path(engine._prepare_sam3_frames())
            self.assertEqual(
                sorted(item.name for item in resource_path.iterdir()),
                ["00000.jpg", "00001.jpg", "00002.jpg"],
            )
            before = [item.stat().st_mtime_ns for item in resource_path.iterdir()]
            self.assertEqual(Path(engine._prepare_sam3_frames()), resource_path)
            after = [item.stat().st_mtime_ns for item in resource_path.iterdir()]
            self.assertEqual(before, after)

    def test_prepared_frame_path_is_absolute_for_isolated_worker_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            previous_cwd = os.getcwd()
            try:
                os.chdir(folder)
                engine = self.make_engine(Path("."))
                for index in range(3):
                    (Path(engine.core.frames_dir) / ("%05d.png" % index)).write_bytes(
                        b"png"
                    )
                resource_path = Path(engine._prepare_sam3_frames())
                self.assertTrue(resource_path.is_absolute())
                self.assertTrue(resource_path.is_dir())
            finally:
                os.chdir(previous_cwd)

    def test_rejects_incomplete_decoded_sequence_before_starting_sam3(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            (Path(engine.core.frames_dir) / "00000.png").write_bytes(b"png")
            with self.assertRaisesRegex(RuntimeError, "decoded frame 1"):
                engine._prepare_sam3_frames()
            self.assertFalse(engine.sam3_frames_dir.exists())
            self.assertFalse(engine.sam3_frames_dir.with_name("sam3_frames.part").exists())

    def test_point_history_is_batched_for_official_refinement_api(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.sam3_prompt({
                "type": "point", "frame": 1, "object_id": 4,
                "x": 0.2, "y": 0.25, "label": "positive",
            })
            engine.sam3_prompt({
                "type": "point", "frame": 1, "object_id": 4,
                "x": 0.7, "y": 0.75, "label": "negative",
            })
            prompt = engine.sam3_bridge.calls[-1][1]["prompt"]
            self.assertEqual(prompt["points"], [[0.2, 0.25], [0.7, 0.75]])
            self.assertEqual(prompt["labels"], ["positive", "negative"])
            self.assertEqual(len(engine.points), 2)
            self.assertFalse(any(point.get("synthetic") for point in engine.points))

    def test_examples_accumulate_as_labeled_visual_boxes(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.sam3_prompt({
                "type": "example", "frame": 0, "object_id": 0,
                "x1": 0.1, "y1": 0.1, "x2": 0.4, "y2": 0.5,
                "label": "positive",
            })
            engine.sam3_prompt({
                "type": "example", "frame": 0, "object_id": 0,
                "x1": 0.6, "y1": 0.2, "x2": 0.9, "y2": 0.7,
                "label": "negative",
            })
            examples = engine.sam3_bridge.calls[-1][1]["prompt"]["examples"]
            self.assertEqual(len(examples), 2)
            self.assertEqual([item["label"] for item in examples], ["positive", "negative"])

    def test_switching_to_semantic_prompt_resets_upstream_session(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.sam3_prompt({
                "type": "point", "frame": 0, "object_id": 0,
                "x": 0.2, "y": 0.3, "label": "positive",
            })
            engine.sam3_prompt({
                "type": "example", "frame": 0, "object_id": 0,
                "x1": 0.1, "y1": 0.1, "x2": 0.6, "y2": 0.8,
                "label": "positive",
            })
            commands = [command for command, _payload in engine.sam3_bridge.calls]
            self.assertEqual(commands, ["add_prompt", "reset_session", "add_prompt"])
            self.assertEqual([item["type"] for item in engine.sam3_prompts], ["example"])

    def test_point_refinement_preserves_semantic_prompt_session(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.sam3_prompt({
                "type": "text", "frame": 0, "object_id": 0, "text": "person",
            })
            engine.sam3_prompt({
                "type": "point", "frame": 0, "object_id": 2,
                "x": 0.4, "y": 0.5, "label": "negative",
            })
            commands = [command for command, _payload in engine.sam3_bridge.calls]
            self.assertEqual(commands, ["add_prompt", "add_prompt"])
            self.assertEqual(
                [item["type"] for item in engine.sam3_prompts], ["text", "point"]
            )
            self.assertEqual(engine.sam3_bridge.calls[-1][1]["obj_id"], 2)

    def test_text_detection_registers_hidden_object_anchors(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            response = engine.sam3_prompt({
                "type": "text", "frame": 0, "object_id": 0, "text": "person",
            })
            self.assertEqual(response["object_ids"], [2, 3])
            self.assertEqual(
                sorted(point["object_id"] for point in engine.points), [2, 3]
            )
            self.assertTrue(all(point["synthetic"] for point in engine.points))
            self.assertTrue((Path(engine.core.mask_dir) / "00000" / "2.png").is_file())

    def test_raw_mask_transport_is_bounded_and_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.sam3_transport_dir.mkdir()
            raw_path = engine.sam3_transport_dir / "mask.bin"
            mask = np.ones((1, 4, 6), dtype=bool)
            raw_path.write_bytes(mask.tobytes())
            result = {
                "frame_index": 2,
                "outputs": {
                    "out_obj_ids": [7],
                    "out_binary_masks": {
                        "encoding": "raw",
                        "dtype": "bool",
                        "shape": [1, 4, 6],
                        "path": str(raw_path),
                    },
                },
            }
            self.assertEqual(engine._write_sam3_result(result, 0), [7])
            self.assertFalse(raw_path.exists())
            self.assertTrue((Path(engine.core.mask_dir) / "00002" / "7.png").is_file())
            outside = Path(folder).parent / "unsafe-mask.bin"
            outside.write_bytes(mask.tobytes())
            self.addCleanup(lambda: outside.unlink(missing_ok=True))
            result["outputs"]["out_binary_masks"]["path"] = str(outside)
            with self.assertRaisesRegex(RuntimeError, "unsafe transport path"):
                engine._write_sam3_result(result, 0)

    def test_empty_detection_result_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            result = {
                "frame_index": 0,
                "outputs": {
                    "out_obj_ids": [],
                    "out_binary_masks": [],
                },
            }
            self.assertEqual(engine._write_sam3_result(result, 0), [])
            self.assertEqual(list(Path(engine.core.mask_dir).iterdir()), [])

    def test_propagation_reports_completion_and_writes_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            stale = Path(engine.core.mask_dir) / "00001" / "99.png"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale")
            engine.sam3_prompts = [{
                "type": "point", "frame": 0, "object_id": 0,
                "x": 0.5, "y": 0.5, "label": "positive",
            }]
            payload = engine.propagate()
            deadline = time.time() + 2
            job = None
            while time.time() < deadline:
                job = engine.get_job(payload["id"])
                if job["status"] not in ("queued", "running"):
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "complete", job.get("error"))
            self.assertEqual(job["progress"], 100)
            self.assertTrue((Path(engine.core.mask_dir) / "00002" / "0.png").is_file())
            self.assertFalse(stale.exists())

    def test_invalid_box_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            with self.assertRaisesRegex(service.ApiError, "positive width"):
                engine.sam3_prompt({
                    "type": "box", "frame": 0, "object_id": 0,
                    "x1": 0.7, "y1": 0.2, "x2": 0.2, "y2": 0.8,
                })
        token = "hf_private_value"
        redacted = service.Engine._redact_token("failed " + token, token)
        self.assertNotIn(token, redacted)
        self.assertIn("[redacted]", redacted)

    def test_model_catalog_never_contains_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.make_engine(Path(folder))
            engine.install_root = Path(folder)
            engine.sam3_repo = Path(folder) / "runtime" / "SAM3.1"
            engine.sam3_python = engine.sam3_repo / ".venv" / "Scripts" / "python.exe"
            engine.sam3_checkpoint = engine.sam3_repo / "models" / "sam3.1_multiplex.pt"
            engine.backend = "cu130"
            catalog = engine.models()
            serialized = __import__("json").dumps(catalog)
            self.assertIn("SAM3.1", serialized)
            self.assertNotIn("hf_token", serialized)
            self.assertNotIn("HF_TOKEN", serialized)

    def test_status_distinguishes_valid_runtime_from_pending_weights(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            engine = self.make_engine(root)
            engine.install_root = root
            engine.backend = "cu130"
            engine.sam3_repo = root / "runtime" / "SAM3.1"
            engine.sam3_python = engine.sam3_repo / ".venv" / "Scripts" / "python.exe"
            engine.sam3_checkpoint = engine.sam3_repo / "models" / "sam3.1_multiplex.pt"
            (engine.sam3_repo / "sam3").mkdir(parents=True)
            (engine.sam3_repo / "sam3" / "model_builder.py").write_text("", encoding="utf-8")
            engine.sam3_python.parent.mkdir(parents=True)
            engine.sam3_python.write_bytes(b"python")

            with mock.patch.object(service.os, "name", "nt"):
                status = engine.sam3_status()

            self.assertTrue(status["source_installed"])
            self.assertTrue(status["runtime_installed"])
            self.assertFalse(status["weights_available"])
            self.assertFalse(status["available"])
            self.assertEqual(status["availability"], "unavailable")
            self.assertEqual(status["status"], "weights-pending")

    def test_setup_passes_token_only_in_child_environment_and_redacts_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            engine = self.make_engine(root)
            engine.install_root = root
            engine.backend = "cu130"
            engine.sam3_install_job_id = None
            manager = root / "installer" / "manage-sam31.ps1"
            manager.parent.mkdir()
            manager.write_text("# test manager", encoding="utf-8")
            engine.sam3_status = lambda: {
                "supported": True, "available": False, "reason": "not installed"
            }
            captured = {}

            class FailedProcess:
                stdout = [
                    "SAMOSA_SAM31 {\"event\": \"started\"}\n",
                    "SAMOSA_SAM31 {\"event\": \"error\", \"message\": \"Access denied for hf_test_secret_value\", \"error_type\": \"RuntimeError\"}\n",
                    "M3.1\\\\.venv\\\\Scripts\\\\python.exe\n",
                ]

                def __init__(self, command, **kwargs):
                    captured["command"] = list(command)
                    captured["environment"] = dict(kwargs["env"])

                @staticmethod
                def wait():
                    return 1

            with mock.patch.object(service.subprocess, "Popen", FailedProcess):
                started = engine.setup_sam3({
                    "accept_license": True,
                    "hf_token": "hf_test_secret_value",
                })
                deadline = time.time() + 2
                job = engine.get_job(started["id"])
                while job["status"] in ("queued", "running") and time.time() < deadline:
                    time.sleep(0.01)
                    job = engine.get_job(started["id"])

            self.assertEqual(job["status"], "failed")
            self.assertEqual(captured["environment"]["HF_TOKEN"], "hf_test_secret_value")
            self.assertNotIn("hf_test_secret_value", " ".join(captured["command"]))
            self.assertNotIn("hf_test_secret_value", __import__("json").dumps(job))
            self.assertIn("[redacted]", job["error"])
            self.assertIn("Access denied", job["message"])
            self.assertNotIn("python.exe", job["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
