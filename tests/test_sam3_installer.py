import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "installer" / "bootstrap.ps1"
SAM31_MANAGER = ROOT / "installer" / "manage-sam31.ps1"
SAM31_DOWNLOADER = ROOT / "installer" / "sam31_download.py"
MAC_BOOTSTRAP = ROOT / "installer" / "macos" / "bootstrap.sh"


class Sam31InstallerTests(unittest.TestCase):
    def test_pins_are_consistent_across_installers_and_not_old(self):
        files = [
            BOOTSTRAP,
            ROOT / "installer" / "macos" / "bootstrap.sh",
            ROOT / "NOTICE.md",
            ROOT / "THIRD_PARTY_NOTICES.md",
        ]
        text = [path.read_text(encoding="utf-8") for path in files]
        for value in (
            "6f2213dfd8bf15c54ef41b9c9eff7af2cfee1c06",
            "829670BBDE1A92445DCF3D6716D94E35729974799B058C5169E17125A906BE59",
        ):
            self.assertTrue(all(value in item for item in text), value)
        self.assertIn("660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7", SAM31_MANAGER.read_text(encoding="utf-8"))
        self.assertIn("daa63191845a41281374e725f4c9e51c7a824460", SAM31_MANAGER.read_text(encoding="utf-8"))
        self.assertNotIn("129a0a54950d71b535cdcdbd06090c5583e293d9", "\n".join(text))

    def test_checkpoint_integrity_and_isolated_runtime_markers(self):
        code = SAM31_DOWNLOADER.read_text(encoding="utf-8")
        compile(code, str(SAM31_DOWNLOADER), "exec")
        self.assertIn("EXPECTED_BYTES = 3502755717", code)
        self.assertIn("0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6", code)
        manager = SAM31_MANAGER.read_text(encoding="utf-8")
        self.assertIn('runtime\\SAM3.1', manager)
        self.assertIn(".samosa-sam31-source.json", manager)
        self.assertIn("sam31-state.json", manager)
        self.assertIn("numpy>=1.26,<2", manager)
        self.assertIn('$Sam31TorchVersion = "2.10.0"', manager)
        self.assertIn('$Sam31TorchvisionVersion = "0.25.0"', manager)
        self.assertIn('$Sam31EinopsVersion = "0.8.1"', manager)
        self.assertIn('$Sam31TritonWindowsVersion = "3.6.0.post26"', manager)
        self.assertIn('$Sam31PycocotoolsVersion = "2.0.10"', manager)
        self.assertIn('$Sam31PsutilVersion = "7.2.1"', manager)
        self.assertIn('import einops, psutil, pycocotools, torch, torchvision, triton', manager)
        self.assertIn("edt_triton(_probe)", manager)
        self.assertIn("torch.cuda.synchronize()", manager)
        self.assertIn("preserved-sam31-models-", manager)
        self.assertIn("environment_revision", manager)
        self.assertIn("-e", manager)
        self.assertNotIn("local_dir_use_symlinks", code)

    def test_downloader_reports_structured_redacted_errors(self):
        spec = importlib.util.spec_from_file_location("samosa_sam31_download", SAM31_DOWNLOADER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        secret = "hf_test_secret_value"
        fake_hub = types.ModuleType("huggingface_hub")

        def fail_download(**kwargs):
            self.assertNotIn("local_dir_use_symlinks", kwargs)
            raise RuntimeError("Access denied for " + secret)

        fake_hub.hf_hub_download = fail_download
        with tempfile.TemporaryDirectory(prefix="samosa-sam31-download-") as folder:
            output = io.StringIO()
            argv = [str(SAM31_DOWNLOADER), "--destination", str(Path(folder) / "model.pt")]
            with mock.patch.dict(sys.modules, {"huggingface_hub": fake_hub}), mock.patch.dict(
                os.environ, {"HF_TOKEN": secret}, clear=False
            ), mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                result = module.main()

        self.assertEqual(result, 3)
        serialized = output.getvalue()
        self.assertNotIn(secret, serialized)
        events = [
            json.loads(line.removeprefix("SAMOSA_SAM31 "))
            for line in serialized.splitlines()
            if line.startswith("SAMOSA_SAM31 ")
        ]
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["error_type"], "RuntimeError")
        self.assertEqual(events[-1]["error_code"], "download_failed")
        self.assertIn("[redacted]", events[-1]["message"])

    def test_downloader_translates_gated_access_403(self):
        spec = importlib.util.spec_from_file_location("samosa_sam31_gated", SAM31_DOWNLOADER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        message, code = module.classify_error(RuntimeError(
            "403 Client Error. Cannot access gated repo. "
            "Access to model facebook/sam3.1 is restricted and you are not in the authorized list."
        ))
        self.assertEqual(code, "gated_access_denied")
        self.assertIn("same account", message)
        self.assertIn("wait until access is granted", message)
        self.assertNotIn("403 Client Error", message)

    def test_hidden_bootstrap_never_prompts_for_hf_token(self):
        bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        manager = SAM31_MANAGER.read_text(encoding="utf-8")
        self.assertIn("-NonInteractive", bootstrap)
        self.assertIn("[switch]$NonInteractive", manager)
        self.assertIn("if ($NonInteractive) { return $null }", manager)
        self.assertNotIn("HF_TOKEN", bootstrap)
        self.assertIn("$env:HF_TOKEN", manager)
        self.assertIn("SAMOSA_CORE_RUNTIME", manager)
        self.assertIn("SAMOSA_CORE_RUNTIME", bootstrap)
        state_block = manager.split("function Write-State", 1)[1].split("$InstallRoot", 1)[0]
        self.assertNotIn("$Token", state_block)

    def test_inno_exposes_sam31_only_as_an_explicit_windows_nvidia_option(self):
        script = (ROOT / "installer" / "Samosa.iss").read_text(encoding="utf-8")
        for marker in (
            "Prepare SAM 3.1 (Windows NVIDIA only)",
            "I acknowledge the Meta SAM License",
            "Sam31Page.Values[0]",
            "-IncludeSam31 -AcceptSam31License",
            "BackendPage.SelectedValueIndex <= 1",
        ):
            self.assertIn(marker, script)
        self.assertIn("ModePage.SelectedValueIndex <> 0", script)
        self.assertIn("PageID = Sam31Page.ID", script)

    def test_macos_scripts_report_unsupported_without_sam31_install_path(self):
        bootstrap = MAC_BOOTSTRAP.read_text(encoding="utf-8")
        manager = (ROOT / "installer" / "macos" / "manage-sam31.sh").read_text(encoding="utf-8")
        self.assertIn("SAM 3.1 is unsupported on macOS", bootstrap)
        self.assertIn('"sam31_supported":false', bootstrap)
        self.assertIn("unsupported on macOS", manager)
        self.assertIn("SAM31_REQUESTED", bootstrap.upper())

    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is required")
    def test_windows_dry_run_enforces_acceptance_mode_and_platform(self):
        def run(*args):
            with tempfile.TemporaryDirectory(prefix="samosa-sam31-plan-") as folder:
                return subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(BOOTSTRAP),
                        "-InstallRoot",
                        folder,
                        "-InstallMode",
                        "Custom",
                        "-Models",
                        "Base",
                        *args,
                        "-DryRun",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=30,
                )

        rejected = run("-IncludeSam31")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("explicit acknowledgment", rejected.stderr)

        accepted = run("-IncludeSam31", "-AcceptSam31License")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        plan = json.loads(accepted.stdout)
        self.assertTrue(plan["sam31_requested"])
        self.assertTrue(plan["sam31_supported"])
        self.assertTrue(plan["sam31_license_acknowledged"])
        self.assertEqual(plan["sam31_checkpoint_bytes"], 3502755717)

        standard = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BOOTSTRAP),
                "-InstallRoot",
                tempfile.gettempdir(),
                "-InstallMode",
                "Standard",
                "-Models",
                "Base",
                "-IncludeSam31",
                "-AcceptSam31License",
                "-DryRun",
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(standard.returncode, 0)
        self.assertIn("only from Windows Complete or Custom", standard.stderr)

        cpu = run("-Backend", "cpu", "-IncludeSam31", "-AcceptSam31License")
        self.assertNotEqual(cpu.returncode, 0)
        self.assertIn("Windows NVIDIA CUDA backend", cpu.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
