import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "installer" / "manage-corridorkey.ps1"


class CorridorKeyInstallerTests(unittest.TestCase):
    def test_pins_checksums_and_isolated_runtime_are_explicit(self):
        code = MANAGER.read_text(encoding="utf-8")
        for value in (
            "485f5e9ab759ead6138cc990776184b59d6ea971",
            "9DB1ABA4597C739B5C4603D463EC2760984B3A3B74D2B320D1E7D07F2E329B07",
            "cf44a8db3f024bb49fd59daa408848ccf05f7477",
            "C43596DE98CC8866D577C8A9F9EA239ACDF3F7A130FF24E25DB4375A50590436",
            "924783CEDDD293FD470126332CE328166FAEA6C4C8D2F36402D5B95187166AF3",
        ):
            self.assertIn(value, code)
        self.assertIn('runtime\\CorridorKey2.0', code)
        self.assertIn('.venv\\Scripts\\python.exe', code)
        self.assertIn('torch = $TorchVersion', code)
        self.assertIn('"cu128"', code)
        self.assertIn("AcceptCorridorKeyLicense", code)
        self.assertIn("commercial use requires written permission", code.lower())

    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is required")
    def test_dry_run_requires_license_and_reports_nonembedded_assets(self):
        with tempfile.TemporaryDirectory(prefix="samosa-ck-plan-") as folder:
            base = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(MANAGER), "-InstallRoot", folder, "-DryRun",
            ]
            rejected = subprocess.run(base, text=True, capture_output=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            accepted = subprocess.run(
                base + ["-AcceptCorridorKeyLicense"], text=True, capture_output=True, timeout=30
            )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        plan = json.loads(accepted.stdout)
        self.assertTrue(plan["license_required"])
        self.assertFalse(plan["model_assets_embedded"])
        self.assertEqual(plan["checkpoint_bytes"], 2661698720)


if __name__ == "__main__":
    unittest.main(verbosity=2)
