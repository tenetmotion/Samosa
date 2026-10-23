import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "samosa_corridorkey_bridge", ROOT / "backend" / "corridorkey_bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class CorridorKeyBridgeTests(unittest.TestCase):
    def runner(self, root):
        return bridge.CorridorKeyRunner(root / ".venv/Scripts/python.exe", root)

    def test_command_preserves_explicit_hint_and_linear_exr_outputs(self):
        with tempfile.TemporaryDirectory(prefix="samosa-ck-command-") as folder:
            root = Path(folder)
            command = self.runner(root).build_command(
                input_dir=root / "frames", output_dir=root / "out", temp_dir=root / "temp",
                fps=23.976, hint_mode="tracked", hint_path=root / "hints",
                hint_quality=1.5, temporal="low_vram", model_scale=0.5, despill=-1,
            )
        self.assertIn("--hint_video", command)
        self.assertIn("--cutout_linear", command)
        self.assertEqual(command[command.index("--outputs") + 1:command.index("--output_format")], ["alpha", "cutout"])
        self.assertEqual(command[command.index("--hint_quality") + 1], "1.0")
        self.assertEqual(command[command.index("--despill_strength") + 1], "0.0")
        self.assertIn("--low_vram", command)

    def test_automatic_mode_omits_hint_and_frame_mode_is_independent(self):
        with tempfile.TemporaryDirectory(prefix="samosa-ck-auto-") as folder:
            root = Path(folder)
            command = self.runner(root).build_command(
                input_dir=root / "frames", output_dir=root / "out", temp_dir=root / "temp",
                fps=24, hint_mode="automatic", temporal="frame",
            )
        self.assertNotIn("--hint_video", command)
        self.assertNotIn("--hint_first_frame", command)
        self.assertEqual(command[command.index("--hann_chunk") + 1], "1")
        self.assertIn("--no_carry_hint", command)

    def test_progress_and_exact_frame_discovery(self):
        self.assertEqual(bridge.CorridorKeyRunner.progress_from_line("HANN stage1", 10), 50)
        self.assertEqual(bridge.CorridorKeyRunner.progress_from_line("window 3/5", 10), 71)
        with tempfile.TemporaryDirectory(prefix="samosa-ck-output-") as folder:
            output = Path(folder) / "clip_alpha_exr"
            output.mkdir()
            for index in range(2):
                (output / ("alpha_%06d.exr" % index)).write_bytes(b"exr")
            found = bridge.CorridorKeyRunner.discover_frames(Path(folder), "alpha", 2)
            self.assertEqual(len(found), 2)
            with self.assertRaises(bridge.CorridorKeyError):
                bridge.CorridorKeyRunner.discover_frames(Path(folder), "alpha", 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
