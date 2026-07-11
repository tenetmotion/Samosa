import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginContractTests(unittest.TestCase):
    def test_manifest_targets_after_effects_panel(self):
        root = ET.parse(ROOT / "panel" / "CSXS" / "manifest.xml").getroot()
        host = root.find(".//Host")
        self.assertEqual(host.attrib["Name"], "AEFT")
        self.assertEqual(root.find(".//Type").text, "Panel")
        self.assertEqual(root.attrib["ExtensionBundleId"], "com.tenet.samosa.roto")
        self.assertEqual(root.attrib["ExtensionBundleName"], "Samosa")
        self.assertEqual(root.attrib["ExtensionBundleVersion"], "1.1.0")
        params = [node.text for node in root.findall(".//Parameter")]
        self.assertIn("--enable-nodejs", params)

    def test_panel_exposes_complete_workflow(self):
        html = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
        required_ids = {
            "viewer", "viewerScale", "objectPanel", "mattingPanel", "removePanel", "outputPanel",
            "positiveTool", "negativeTool", "objectId", "propagate", "clearTracking", "trackingStatus", "runMatting",
            "runRemoval", "exportImport", "cancelJob", "samModel", "mattingModel", "deduplicate",
            "removalMethod", "outputFormat", "outputDestination", "chooseExportDestination", "clearExportDestination",
        }
        found = set(re.findall(r'id="([^"]+)"', html))
        self.assertFalse(required_ids - found, "Missing controls: %s" % sorted(required_ids - found))

    def test_redesign_assets_and_tokens_are_release_safe(self):
        html = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "panel" / "css" / "panel.css").read_text(encoding="utf-8").lower()
        logo = ROOT / "panel" / "assets" / "samosa_logo.svg"
        self.assertTrue(logo.is_file())
        self.assertIn('src="assets/samosa_logo.svg"', html)
        for token in ("#ff5722", "#0a0a0a", "#111111", "#2a2a2a", "#ececec"):
            self.assertIn(token, css)
        self.assertNotIn("@import", css)
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)

    def test_progress_layout_uses_non_overlapping_rows(self):
        html = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "panel" / "css" / "panel.css").read_text(encoding="utf-8")
        self.assertIn('class="job-bar-header"', html)
        self.assertIn('class="job-bar-actions"', html)
        self.assertRegex(css, r"\.job-bar progress\s*\{[^}]*width:\s*100%")
        job_css = "\n".join(line for line in css.splitlines() if ".job-bar" in line)
        self.assertNotIn("float:", job_css)
        self.assertNotRegex(job_css, r"margin-top:\s*-\d")
        self.assertNotRegex(job_css, r"width:\s*calc\(")

    def test_host_script_is_es3_and_undo_wrapped(self):
        jsx = (ROOT / "panel" / "host" / "host.jsx").read_text(encoding="utf-8")
        forbidden = [r"\blet\b", r"\bconst\b", r"=>", r"\.map\(", r"\.filter\(", r"\.forEach\(", r"Object\.keys\("]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, jsx), pattern)
        self.assertIn('property("ADBE Transform Group")', jsx)
        self.assertIn("app.beginUndoGroup", jsx)
        self.assertIn("app.endUndoGroup", jsx)

    def test_service_routes_cover_workflow(self):
        code = (ROOT / "backend" / "service.py").read_text(encoding="utf-8")
        for route in ("/api/load", "/api/points", "/api/tracking/clear", "/api/propagate", "/api/dedupe", "/api/matting", "/api/removal", "/api/export", "/api/job/cancel"):
            self.assertIn(route, code)
        self.assertIn('payload.get("output_dir"', code)
        for tag in ("segmentation_alpha", "segmentation_matte", "segmentation_bgcolor", "matting_alpha", "matting_matte", "matting_bgcolor", "object_removal"):
            self.assertIn('"%s"' % tag, code)

    def test_output_name_field_starts_as_source_name_placeholder(self):
        html = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="outputName" type="text" value="" placeholder="Source file name"', html)
        panel = (ROOT / "panel" / "js" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("setDefaultOutputName(info.sourceName || info.path)", panel)

    def test_release_tree_contains_no_machine_specific_paths(self):
        checked = [
            ROOT / "README.md", ROOT / "install.ps1", ROOT / "backend" / "service.py",
            ROOT / "panel" / "js" / "panel.js", ROOT / "tests" / "test_service.py",
        ]
        content = "\n".join(path.read_text(encoding="utf-8") for path in checked)
        self.assertNotIn("C:\\Users\\", content)
        self.assertNotIn("jaket", content.lower())

    def test_generated_config_is_not_committed(self):
        self.assertFalse((ROOT / "panel" / "config.json").exists())
        self.assertTrue((ROOT / "panel" / "config.example.json").exists())

    def test_public_install_and_tutorial_docs_are_present(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        install = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        tutorial = (ROOT / "docs" / "AFTER_EFFECTS_TUTORIAL.md").read_text(encoding="utf-8")
        self.assertIn("docs/INSTALL.md", readme)
        self.assertIn("docs/AFTER_EFFECTS_TUTORIAL.md", readme)
        self.assertIn("docs/MODELS.md", readme)
        self.assertIn("Sammie-Roto-2", install)
        self.assertIn("Third-party notices", install)
        self.assertIn("Track objects", tutorial)
        self.assertIn("Export and add to comp", tutorial)
        self.assertTrue((ROOT / "docs" / "MODELS.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
