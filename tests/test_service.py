import importlib.util
import http.client
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SAMMIE_REPO = Path(os.environ.get("SAMMIE_REPO", str(PLUGIN_ROOT.parent / "Sammie-Roto-2")))
spec = importlib.util.spec_from_file_location("samosa_ae_service", PLUGIN_ROOT / "backend" / "service.py")
service = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = service
spec.loader.exec_module(service)

try:
    import av as media_av
    import cv2 as media_cv2
    import numpy as media_np
except ModuleNotFoundError:
    media_av = media_cv2 = media_np = None


class ServiceUnitTests(unittest.TestCase):
    def test_frame_bounds_are_inclusive_exclusive_and_float_stable(self):
        self.assertEqual(service._calculate_frame_bounds(2 / 24, 5 / 24, 24), (2 / 24, 5 / 24, 2, 5))
        self.assertEqual(service._calculate_frame_bounds(0.1, 0.2, 10), (0.1, 0.2, 1, 2))
        self.assertEqual(service._calculate_frame_bounds(None, 1 / 24, 24)[2:], (0, 1))
        self.assertEqual(service._calculate_frame_bounds(2 / 24, None, 24)[2:], (2, None))
        with self.assertRaises(service.ApiError):
            service._calculate_frame_bounds(float("nan"), 1, 24)
        with self.assertRaises(service.ApiError):
            service._calculate_frame_bounds(1, float("inf"), 24)
        with self.assertRaises(service.ApiError):
            service._calculate_frame_bounds(1, 1, 24)

    def test_client_watchdog_keeps_shared_service_until_all_panels_exit(self):
        self.assertTrue(service._parent_process_exists(os.getpid()))
        self.assertFalse(service._parent_process_exists(2147483647))
        stopped = threading.Event()

        class FakeServer:
            def shutdown(self):
                stopped.set()

        registry = service.ClientRegistry()
        with mock.patch.object(service, "_parent_process_exists", return_value=True):
            self.assertEqual(registry.register("panel-a", 1234), 1)
            self.assertEqual(registry.register("panel-b", 5678), 2)
            self.assertEqual(registry.release("panel-a"), 1)
            self.assertFalse(registry.unused_for(0))
        with mock.patch.object(service, "_parent_process_exists", return_value=False):
            stop_event, thread = service.start_client_watchdog(
                FakeServer(), registry, poll_interval=0.01, grace_period=0
            )
            self.assertTrue(stopped.wait(1), "watchdog did not stop the server")
            stop_event.set()
            thread.join(1)

        grace_registry = service.ClientRegistry()
        with mock.patch.object(service, "_parent_process_exists", return_value=True):
            grace_registry.register("reload-panel", 1234)
        with mock.patch.object(service.time, "monotonic", side_effect=[100.0, 101.0, 102.1]):
            self.assertEqual(grace_registry.release("reload-panel"), 0)
            self.assertFalse(grace_registry.unused_for(2.0))
            self.assertTrue(grace_registry.unused_for(2.0))

        reload_registry = service.ClientRegistry()
        with mock.patch.object(service, "_parent_process_exists", return_value=True):
            self.assertEqual(reload_registry.register("old-document", 1234), 1)
            self.assertEqual(reload_registry.register("new-document", 1234), 1)
        self.assertNotIn("old-document", reload_registry.clients)
        self.assertIn("new-document", reload_registry.clients)
        self.assertEqual(reload_registry.release("new-document"), 0)

    def test_trim_decode_seeks_filters_and_stops_at_exclusive_end(self):
        class FakeFrame:
            def __init__(self, pts):
                self.pts = pts

        class FakeStream:
            start_time = 0
            time_base = Fraction(1, 24)

        class FakeContainer:
            def __init__(self):
                self.seek_calls = []
                self.decoded_pts = []

            def seek(self, *args, **kwargs):
                self.seek_calls.append((args, kwargs))

            def decode(self, stream):
                for pts in range(8, 30):
                    self.decoded_pts.append(pts)
                    yield FakeFrame(pts)

        engine = object.__new__(service.Engine)
        written = []
        engine._write_decoded_frame = lambda frame, output, frame_format: written.append(
            (frame.pts, output, frame_format)
        )
        container = FakeContainer()
        count = engine._decode_video(container, FakeStream(), "png", 10, 13, 24)

        self.assertEqual(count, 3)
        self.assertEqual(written, [(10, 0, "png"), (11, 1, "png"), (12, 2, "png")])
        self.assertEqual(container.decoded_pts[-1], 13)
        self.assertEqual(len(container.seek_calls), 1)
        self.assertTrue(container.seek_calls[0][1]["backward"])

        class VfrStream:
            start_time = 0
            time_base = Fraction(1, 1000)

        class VfrContainer(FakeContainer):
            def decode(self, stream):
                for pts in (0, 30, 90, 120, 160):
                    self.decoded_pts.append(pts)
                    yield FakeFrame(pts)

        vfr_written = []
        engine._write_decoded_frame = lambda frame, output, frame_format: vfr_written.append(
            frame.pts
        )
        vfr_count = engine._decode_video(VfrContainer(), VfrStream(), "png", 0, 4, 24)
        self.assertEqual(vfr_count, 4)
        self.assertEqual(vfr_written, [0, 30, 30, 120])

        interpreted_written = []
        engine._write_decoded_frame = lambda frame, output, frame_format: interpreted_written.append(
            frame.pts
        )
        class InterpretedContainer(FakeContainer):
            def decode(self, stream):
                for pts in range(0, 10):
                    self.decoded_pts.append(pts)
                    yield FakeFrame(pts)

        interpreted_count = engine._decode_video(
            InterpretedContainer(), FakeStream(), "png", 2, 5, 24
        )
        self.assertEqual(interpreted_count, 3)
        self.assertEqual(interpreted_written, [2, 3, 4])

    def test_load_route_forwards_optional_source_range(self):
        calls = []

        class FakeEngine:
            def load_media(self, *args):
                calls.append(args)
                return {"loaded": True}

            def __getattr__(self, name):
                return lambda *args, **kwargs: {}

        old_engine = service.Handler.engine
        service.Handler.engine = FakeEngine()
        server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request(
                "POST", "/api/load",
                body=json.dumps({
                    "path": "clip.mov", "source_start": 1.25,
                    "source_end": 2.5, "source_fps": 23.976,
                    "source_native_fps": 24,
                }),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200, body)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)
            service.Handler.engine = old_engine
        self.assertEqual(calls, [("clip.mov", 1.25, 2.5, 23.976, 24)])

    def test_load_media_rejects_partial_source_ranges_before_engine_use(self):
        engine = object.__new__(service.Engine)
        with self.assertRaisesRegex(service.ApiError, "provided together"):
            engine.load_media("missing.mov", source_start=1.0)


@unittest.skipUnless(
    media_av is not None and media_cv2 is not None and media_np is not None,
    "PyAV/OpenCV media fixture dependencies are unavailable",
)
class MediaDecodeTests(unittest.TestCase):
    def test_real_cfr_trim_decodes_exact_source_frames(self):
        with tempfile.TemporaryDirectory(prefix="samosa-media-decode-") as folder:
            folder = Path(folder)
            video_path = folder / "source.mp4"
            frames_dir = folder / "frames"
            frames_dir.mkdir()

            container = media_av.open(str(video_path), mode="w")
            stream = container.add_stream("mpeg4", rate=Fraction(24, 1))
            stream.width = 64
            stream.height = 48
            stream.pix_fmt = "yuv420p"
            try:
                for frame_number in range(8):
                    array = media_np.full(
                        (48, 64, 3), frame_number * 24, dtype=media_np.uint8
                    )
                    frame = media_av.VideoFrame.from_ndarray(array, format="rgb24")
                    frame.pts = frame_number
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            finally:
                container.close()

            engine = object.__new__(service.Engine)
            engine.cv2 = media_cv2
            engine.core = SimpleNamespace(frames_dir=str(frames_dir))
            container = media_av.open(str(video_path))
            try:
                stream = container.streams.video[0]
                count = engine._decode_video(container, stream, "png", 2, 5, 24)
            finally:
                container.close()

            self.assertEqual(count, 3)
            decoded = [
                media_cv2.imread(str(frames_dir / ("%05d.png" % frame_number)))
                for frame_number in range(3)
            ]
            means = [float(frame.mean()) for frame in decoded]
            self.assertGreater(means[0], 35.0)
            self.assertLess(means[0], 65.0)
            self.assertGreater(means[2], 75.0)
            self.assertLess(means[2], 125.0)


class ServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SAMMIE_REPO / "sammie" / "sammie.py").is_file():
            raise unittest.SkipTest("Set SAMMIE_REPO to run integration tests")
        cls.session_temp = tempfile.TemporaryDirectory(prefix="samosa-ae-session-")
        cls.previous_session_root = os.environ.get("SAMOSA_SESSION_ROOT")
        os.environ["SAMOSA_SESSION_ROOT"] = cls.session_temp.name
        try:
            cls.engine = service.Engine(SAMMIE_REPO, mock=True)
        except ModuleNotFoundError as exc:
            cls.session_temp.cleanup()
            if cls.previous_session_root is None:
                os.environ.pop("SAMOSA_SESSION_ROOT", None)
            else:
                os.environ["SAMOSA_SESSION_ROOT"] = cls.previous_session_root
            raise unittest.SkipTest("Sammie integration dependencies are unavailable: %s" % exc)
        cls.temp = tempfile.TemporaryDirectory(prefix="samosa-ae-test-")
        cls.image_path = Path(cls.temp.name) / "source.png"
        image = cls.engine.np.zeros((180, 320, 3), dtype=cls.engine.np.uint8)
        image[:, :] = (24, 28, 34)
        cls.engine.cv2.rectangle(image, (90, 45), (220, 155), (35, 160, 235), -1)
        cls.engine.cv2.imwrite(str(cls.image_path), image)

    @classmethod
    def tearDownClass(cls):
        if not hasattr(cls, "engine"):
            return
        cls.temp.cleanup()
        if cls.previous_session_root is None:
            os.environ.pop("SAMOSA_SESSION_ROOT", None)
        else:
            os.environ["SAMOSA_SESSION_ROOT"] = cls.previous_session_root
        cls.session_temp.cleanup()
        if os.path.exists(cls.engine.core.temp_dir):
            import shutil
            shutil.rmtree(cls.engine.core.temp_dir)

    def wait_job(self, payload, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.engine.get_job(payload["id"])
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        self.fail("Job timed out: %s" % payload["kind"])

    def test_00_headless_progress_adapter(self):
        job = service.Job("adapter")
        service._progress_context.job = job
        dialog = service.NullProgressDialog("Working", "Cancel", 0, 20, None)
        dialog.setValue(5)
        self.assertEqual(job.progress, 25)
        dialog.setLabelText("Refining matte")
        self.assertEqual(job.message, "Refining matte")
        service._progress_context.job = None

    def test_01_health_and_isolated_session(self):
        health = self.engine.health()
        self.assertTrue(health["ok"])
        self.assertTrue(health["mock"])
        expected_hash = service._backend_sha256()
        self.assertEqual(service.BACKEND_SHA256, expected_hash)
        self.assertEqual(health["backend_sha256"], expected_hash)
        self.assertEqual(Path(self.engine.core.temp_dir), Path(self.session_temp.name))

    def test_02_load_and_select(self):
        state = self.engine.load_media(str(self.image_path))
        self.assertEqual((state["width"], state["height"], state["total_frames"]), (320, 180, 1))
        self.assertFalse(state["trimmed"])
        self.assertEqual((state["source_start_frame"], state["source_end_frame_exclusive"]), (0, 1))
        state = self.engine.add_point({"frame": 0, "object_id": 0, "positive": True, "x": 150, "y": 100})
        state = self.engine.add_point({"frame": 0, "object_id": 0, "positive": False, "x": 190, "y": 100})
        self.assertEqual(len(state["points"]), 2)
        self.assertGreater(len(self.engine.frame_png(0, "edit", 0)), 100)
        self.assertGreater(len(self.engine.frame_png(0, "segmentation-alpha", 0)), 100)

    def test_03_propagate_matting_and_remove(self):
        for starter in (self.engine.propagate, lambda: self.engine.deduplicate(0.8), self.engine.run_matting):
            job = self.wait_job(starter())
            self.assertEqual(job["status"], "complete", job.get("error"))
        job = self.wait_job(self.engine.run_removal("OpenCV"))
        self.assertEqual(job["status"], "complete", job.get("error"))
        self.assertGreater(len(self.engine.frame_png(0, "matting-alpha", 0)), 100)
        self.assertGreater(len(self.engine.frame_png(0, "removal", 0)), 100)

    def test_04_export_png_sequence(self):
        destination = Path(self.temp.name) / "chosen_exports"
        job = self.wait_job(self.engine.export({
            "output_type": "Segmentation-Alpha", "format": "png",
            "object_id": 0, "quality": 14, "name": "integration_test",
            "output_dir": str(destination),
        }))
        self.assertEqual(job["status"], "complete", job.get("error"))
        result = job["result"]
        self.assertTrue(result["sequence"])
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(Path(result["folder"]).parent, destination)
        self.assertEqual(result["frame_count"], 1)
        self.assertEqual(result["fps"], 24.0)
        self.assertFalse(result["trimmed"])
        self.assertEqual(result["source_start_time"], 0.0)

    def test_05_export_location_validation_and_name_sanitizing(self):
        with self.assertRaises(service.ApiError):
            self.engine.export({"output_dir": "relative/path"})
        destination = Path(self.temp.name) / "safe_exports"
        job = self.wait_job(self.engine.export({
            "output_type": "Segmentation-Alpha", "format": "png",
            "object_id": 0, "quality": 14, "name": "../unsafe:name",
            "output_dir": str(destination),
        }))
        self.assertEqual(job["status"], "complete", job.get("error"))
        folder = Path(job["result"]["folder"])
        self.assertEqual(folder.parent, destination)
        self.assertNotIn("..", folder.name)
        self.assertNotIn(":", folder.name)

    def test_06_default_export_names_follow_clip_object_and_output(self):
        self.engine.settings_mgr.set_session_setting("object_names", {"0": "Hero Person"})
        self.assertEqual(
            self.engine._default_export_name("", 0, "Matting-Matte"),
            "source_Hero_Person_matting_matte",
        )
        self.assertEqual(
            self.engine._default_export_name("Custom Base", -1, "ObjectRemoval"),
            "Custom_Base_all_object_removal",
        )
        destination = Path(self.temp.name) / "named_exports"
        job = self.wait_job(self.engine.export({
            "output_type": "Segmentation-Alpha", "format": "png",
            "object_id": 0, "quality": 14, "name": "",
            "output_dir": str(destination),
        }))
        self.assertEqual(job["status"], "complete", job.get("error"))
        folder = Path(job["result"]["folder"])
        self.assertRegex(folder.name, r"^source_Hero_Person_segmentation_alpha_[0-9a-f]{8}$")
        self.assertTrue(Path(job["result"]["path"]).name.startswith("source_Hero_Person_segmentation_alpha."))

    def test_08_trim_video_uses_source_frame_bounds_and_zero_based_output(self):
        video_path = Path(self.temp.name) / "synthetic_cfr.mp4"
        container = self.engine.av.open(str(video_path), mode="w")
        stream = container.add_stream("mpeg4", rate=Fraction(24, 1))
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        try:
            for frame_number in range(8):
                array = self.engine.np.full(
                    (48, 64, 3), frame_number * 24, dtype=self.engine.np.uint8
                )
                frame = self.engine.av.VideoFrame.from_ndarray(array, format="rgb24")
                frame.pts = frame_number
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        finally:
            container.close()

        full_state = self.engine.load_media(str(video_path))
        self.assertEqual(full_state["total_frames"], 8)
        self.assertFalse(full_state["trimmed"])
        self.assertEqual((full_state["source_start_frame"], full_state["source_end_frame_exclusive"]), (0, 8))

        state = self.engine.load_media(
            str(video_path), source_start=2 / 24, source_end=5 / 24
        )
        self.assertEqual(state["total_frames"], 3)
        self.assertTrue(state["trimmed"])
        self.assertEqual(state["source_start_frame"], 2)
        self.assertEqual(state["source_end_frame_exclusive"], 5)
        self.assertAlmostEqual(state["source_start_time"], 2 / 24)
        self.assertAlmostEqual(state["source_end_time"], 5 / 24)

        first = self.engine.cv2.imread(
            os.path.join(self.engine.core.frames_dir, "00000.png"), self.engine.cv2.IMREAD_COLOR
        )
        last = self.engine.cv2.imread(
            os.path.join(self.engine.core.frames_dir, "00002.png"), self.engine.cv2.IMREAD_COLOR
        )
        self.assertGreater(float(first.mean()), 35.0)
        self.assertLess(float(first.mean()), 65.0)
        self.assertGreater(float(last.mean()), 75.0)
        self.assertLess(float(last.mean()), 125.0)

        interpreted = self.engine.load_media(
            str(video_path), source_start=2 / 12, source_end=5 / 12,
            source_fps=12, source_native_fps=24,
        )
        self.assertEqual(interpreted["total_frames"], 3)
        self.assertEqual(interpreted["fps"], 12.0)
        self.assertEqual(
            (interpreted["source_start_frame"], interpreted["source_end_frame_exclusive"]),
            (2, 5),
        )
        interpreted_first = self.engine.cv2.imread(
            os.path.join(self.engine.core.frames_dir, "00000.png"),
            self.engine.cv2.IMREAD_COLOR,
        )
        interpreted_last = self.engine.cv2.imread(
            os.path.join(self.engine.core.frames_dir, "00002.png"),
            self.engine.cv2.IMREAD_COLOR,
        )
        self.assertAlmostEqual(float(interpreted_first.mean()), 48.0, delta=8.0)
        self.assertAlmostEqual(float(interpreted_last.mean()), 96.0, delta=8.0)

    def test_07_settings_validation(self):
        state = self.engine.update_settings({"grow": 3, "matany_model": "MatAnyone2"})
        self.assertEqual(state["settings"]["grow"], 3)
        with self.assertRaises(service.ApiError):
            self.engine.update_settings({"not_a_setting": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
