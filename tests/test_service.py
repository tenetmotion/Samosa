import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SAMMIE_REPO = Path(os.environ.get("SAMMIE_REPO", str(PLUGIN_ROOT.parent / "Sammie-Roto-2")))
spec = importlib.util.spec_from_file_location("samosa_ae_service", PLUGIN_ROOT / "backend" / "service.py")
service = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = service
spec.loader.exec_module(service)


class ServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SAMMIE_REPO / "sammie" / "sammie.py").is_file():
            raise unittest.SkipTest("Set SAMMIE_REPO to run integration tests")
        cls.engine = service.Engine(SAMMIE_REPO, mock=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="samosa-ae-test-")
        cls.image_path = Path(cls.temp.name) / "source.png"
        image = cls.engine.np.zeros((180, 320, 3), dtype=cls.engine.np.uint8)
        image[:, :] = (24, 28, 34)
        cls.engine.cv2.rectangle(image, (90, 45), (220, 155), (35, 160, 235), -1)
        cls.engine.cv2.imwrite(str(cls.image_path), image)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
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
        self.assertRegex(self.engine.core.temp_dir.replace("/", "\\"), r"^temp\\ae_test_\d+$")

    def test_02_load_and_select(self):
        state = self.engine.load_media(str(self.image_path))
        self.assertEqual((state["width"], state["height"], state["total_frames"]), (320, 180, 1))
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

    def test_07_settings_validation(self):
        state = self.engine.update_settings({"grow": 3, "matany_model": "MatAnyone2"})
        self.assertEqual(state["settings"]["grow"], 3)
        with self.assertRaises(service.ApiError):
            self.engine.update_settings({"not_a_setting": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
