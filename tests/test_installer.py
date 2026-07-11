import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "installer" / "bootstrap.ps1"


class InstallerContractTests(unittest.TestCase):
    def dry_run(self, *arguments, expect_success=True):
        with tempfile.TemporaryDirectory(prefix="samosa-installer-plan-") as install_root:
            command = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(BOOTSTRAP), "-InstallRoot", install_root,
                *arguments, "-DryRun",
            ]
            result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        if expect_success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def test_standard_plan_installs_only_base_model(self):
        plan = self.dry_run("-InstallMode", "Standard", "-Models", "Base", "-Backend", "cu130")
        self.assertEqual(plan["models"], ["Base"])
        self.assertFalse(plan["includes_restricted_models"])
        self.assertEqual(plan["backend"], "cu130")
        self.assertEqual(plan["sammie_commit"], "129a0a54950d71b535cdcdbd06090c5583e293d9")
        self.assertEqual(plan["sammie_archive_sha256"], "71CFC39AC389DA6C138E956881DEA1A811F473F63052FB026B8E24CFE28AF62B")

    def test_complete_plan_requires_acceptance_and_contains_all_models(self):
        rejected = self.dry_run("-InstallMode", "Complete", "-Models", "all", expect_success=False)
        self.assertIn("explicit acceptance", rejected.stderr)
        plan = self.dry_run("-InstallMode", "Complete", "-Models", "all", "-AcceptRestrictedModels")
        self.assertTrue(plan["includes_restricted_models"])
        self.assertEqual(len(plan["models"]), 9)
        for key in ("Base", "Large", "Efficient", "matanyone2", "videomama", "svd_vae", "minimax_transformer", "minimax_vae"):
            self.assertIn(key, plan["models"])

    def test_custom_restricted_pack_requires_acceptance(self):
        result = self.dry_run(
            "-InstallMode", "Custom", "-Models", "Base,matanyone2",
            expect_success=False,
        )
        self.assertIn("explicit acceptance", result.stderr)

    def test_inno_wizard_exposes_modes_backends_and_license_gate(self):
        script = (ROOT / "installer" / "Samosa.iss").read_text(encoding="utf-8")
        for text in (
            "Standard - SAM2 Base now", "Complete - pre-download every supported model",
            "Custom - choose individual model packs", "NVIDIA CUDA 13.0",
            "Intel Arc/Xe", "RestrictedSelected", "THIRD_PARTY_NOTICES.md",
        ):
            self.assertIn(text, script)
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn("{localappdata}\\Programs\\Samosa", script)

    def test_model_downloader_uses_upstream_registry(self):
        code = (ROOT / "installer" / "download_models.py").read_text(encoding="utf-8")
        compile(code, "download_models.py", "exec")
        self.assertIn("from sammie.model_downloader import MODEL_REGISTRY", code)
        self.assertIn("Checksum mismatch", code)
        self.assertIn(".part", code)

    def test_standard_on_demand_behavior_is_documented_and_wired(self):
        docs = (ROOT / "docs" / "MODELS.md").read_text(encoding="utf-8")
        panel = (ROOT / "panel" / "js" / "panel.js").read_text(encoding="utf-8")
        service = (ROOT / "backend" / "service.py").read_text(encoding="utf-8")
        self.assertIn("download automatically when first requested", docs.lower())
        self.assertIn("confirmRestrictedModel", panel)
        self.assertIn("activeJob.message || label", panel)
        self.assertIn('job.message = "Downloading %s"', service)


if __name__ == "__main__":
    unittest.main(verbosity=2)
