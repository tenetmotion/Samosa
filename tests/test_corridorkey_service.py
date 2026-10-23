import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "samosa_corridorkey_service", ROOT / "backend" / "service.py"
)
service = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = service
SPEC.loader.exec_module(service)


class CorridorKeyServiceTests(unittest.TestCase):
    def test_optional_settings_remain_samosa_owned(self):
        engine = object.__new__(service.Engine)
        engine.corridorkey_settings = engine._default_corridorkey_settings()
        engine.settings_mgr = SimpleNamespace(
            set_session_setting=lambda *args: self.fail("CorridorKey setting leaked upstream"),
            save_session_settings=lambda: None,
        )
        engine._selected_sam_model = lambda: "Base"
        engine.state = lambda: {"settings": dict(engine.corridorkey_settings)}
        result = engine.update_settings({
            "corridorkey_hint_mode": "automatic",
            "corridorkey_model_scale": 0.5,
        })
        self.assertEqual(result["settings"]["corridorkey_hint_mode"], "automatic")
        self.assertEqual(result["settings"]["corridorkey_model_scale"], 0.5)

    def test_linear_bgra_cutout_converts_to_srgb_rgba(self):
        linear = np.array([[[0.0, 0.0031308, 1.0, 0.5]]], dtype=np.float32)
        engine = object.__new__(service.Engine)
        engine.corridorkey_cutout_frames = ["cutout.exr"]
        engine.np = np
        engine.cv2 = SimpleNamespace(
            IMREAD_UNCHANGED=-1,
            imread=lambda path, mode: linear.copy(),
        )
        rgba = engine._corridorkey_cutout_frame(0)
        self.assertEqual(rgba.shape, (1, 1, 4))
        self.assertEqual(rgba[0, 0].tolist(), [255, 10, 0, 128])

    def test_automatic_matting_does_not_require_points(self):
        engine = object.__new__(service.Engine)
        engine.points = []
        engine.mock = True
        engine.corridorkey_settings = engine._default_corridorkey_settings()
        engine.corridorkey_settings["corridorkey_hint_mode"] = "automatic"
        engine.loaded_path = "clip.mov"
        engine.settings_mgr = SimpleNamespace(
            get_session_setting=lambda key, default=None: "CorridorKey2.0" if key == "matany_model" else default
        )
        engine._new_job = lambda kind, target: kind
        self.assertEqual(engine.run_matting(), "matting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
