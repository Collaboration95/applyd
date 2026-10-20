"""Synthetic checks for workspace setup and prerequisite diagnosis."""

import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_workspace as setup


class SetupWorkspaceTests(unittest.TestCase):
    def test_cli_init_status_and_doctor_in_fresh_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fresh"
            cli = setup.CODE_ROOT / "scripts/workspace.py"
            for command in ("init", "status", "doctor"):
                result = subprocess.run([sys.executable, str(cli), "--workspace", str(root), command], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                if command == "status":
                    self.assertEqual(payload["application_records"], 0)
                if command == "doctor":
                    self.assertNotIn(str(root), result.stdout)
            self.assertTrue((root / "dashboard/data.js").exists())

    def test_init_empty_root_creates_valid_private_defaults_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            result = setup.init_workspace(root)
            self.assertTrue(result["created_files"] > 0)
            self.assertEqual(json.loads((root / "settings.json").read_text()),
                             setup.settings._validate(setup.settings.DEFAULTS))
            self.assertEqual(json.loads((root / "profile/answers.json").read_text()),
                             {"schema_version": 1, "answers": []})
            self.assertEqual(json.loads((root / "projects/catalog.json").read_text()),
                             {"schema_version": 1, "projects": []})
            self.assertEqual(json.loads((root / "resumes/registry.json").read_text()),
                             {"schema_version": 1, "releases": []})
            self.assertEqual(json.loads((root / "resumes/presets.json").read_text()), {})
            for asset in setup.DASHBOARD_ASSETS:
                self.assertEqual((root / "dashboard" / asset).read_bytes(),
                                 (setup.CODE_ROOT / "dashboard" / asset).read_bytes())

    def test_repeated_init_refuses_without_changing_any_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            setup.init_workspace(root)
            before = {path.relative_to(root): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            with self.assertRaises(setup.SetupError):
                setup.init_workspace(root)
            after = {path.relative_to(root): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)

    def test_nonempty_root_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            marker = root / "existing.json"
            marker.write_bytes(b"keep these bytes\n")
            with self.assertRaises(setup.SetupError):
                setup.init_workspace(root)
            self.assertEqual(list(root.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"keep these bytes\n")

    def test_doctor_marks_missing_optional_pdf_as_optional_and_node_as_blocker(self):
        def fake_which(name):
            return {"node": "/private/user/bin/node"}.get(name)

        with patch.object(setup.importlib.util, "find_spec", return_value=None):
            report = setup.doctor(python_version="3.12.1", node_version="17.9.0",
                                  system="Darwin", browser_available=True, which=fake_which)
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual(checks["Python"]["status"], "available")
        self.assertEqual(checks["Node.js"]["status"], "missing")
        self.assertFalse(checks["Node.js"]["required"] is False)
        self.assertEqual(checks["PDF tools"]["status"], "optional_missing")
        self.assertFalse(checks["PDF tools"]["required"])
        self.assertFalse(report["ready"])
        self.assertNotIn("/private", json.dumps(report))
        self.assertNotIn("node", json.dumps(report))

    def test_doctor_ready_with_required_tools_and_non_macos_is_explicit(self):
        report = setup.doctor(python_version="3.11.0", node_version="v20.0.0",
                              system="Linux", which=lambda name: "/usr/bin/tool")
        self.assertTrue(report["ready"])
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual(checks["macOS browser"]["status"], "unsupported")
        self.assertEqual(checks["PDF tools"]["status"], "available")


if __name__ == "__main__":
    unittest.main()
