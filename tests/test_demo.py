import importlib.util
import base64
import json
import re
import tempfile
import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)
GIF_SPEC = importlib.util.spec_from_file_location("demo_gif", ROOT / "scripts" / "demo_gif.py")
demo_gif = importlib.util.module_from_spec(GIF_SPEC)
GIF_SPEC.loader.exec_module(demo_gif)


class DemoTests(unittest.TestCase):
    def test_existing_destination_is_preserved_and_demo_payload_passes_canaries(self):
        import check_publication
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / "keep.txt"
            existing.write_bytes(b"synthetic original bytes")
            with self.assertRaises(ValueError):
                demo.build(Path(temp))
            self.assertEqual(existing.read_bytes(), b"synthetic original bytes")
        payload = demo.synthetic_payload("2026-10-20T12:00:00+08:00")
        self.assertTrue(payload["inbox"]["questions"])
        self.assertEqual(payload["settings"]["mode"], "dry_run")
        scanner = check_publication.Scanner()
        scanner.content("demo/applications.json", json.dumps(payload).encode())
        self.assertTrue(scanner.report()["ok"])
        scanner.content("demo/applications.json", ("PRIVATE" + "_CANARY_" + "SYNTHETIC_DEMO").encode())
        self.assertFalse(scanner.report()["ok"])
    def test_synthetic_data_covers_statuses_runs_outcomes_and_stop_reasons(self):
        apps = demo.synthetic_applications()
        self.assertEqual(len(apps), 15)
        statuses = {app["status"] for app in apps}
        self.assertTrue({"discovered", "preparing", "awaiting_answer", "ready", "submitted", "submission_uncertain", "failed", "skipped", "rejected", "interview", "withdrawn"} <= statuses)
        self.assertEqual(len({app["updated_at"][:10] for app in apps}), 2)
        self.assertTrue(any(app["status"] == "interview" for app in apps))
        self.assertTrue(any(app["status"] == "rejected" for app in apps))
        self.assertTrue(any(app["status"] == "withdrawn" for app in apps))
        text = json.dumps(apps)
        for reason in ("unknown_answer", "page_changed", "eligibility_excluded", "login_required", "submission_uncertain"):
            self.assertIn(reason, text)
        for app in apps:
            for submission in app["submissions"]:
                self.assertRegex(submission["resume"]["id"], r"^demo-resume-\d{2}\.pdf$")

    def test_generator_copies_public_dashboard_and_writes_json_outside_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "demo-output"
            page = demo.build(destination, "2026-10-20T10:00:00+08:00")
            self.assertEqual(page, (destination / "dashboard/index.html").resolve())
            for asset in demo.ASSETS:
                self.assertEqual((destination / "dashboard" / asset).read_bytes(), (ROOT / "dashboard" / asset).read_bytes())
            payload = json.loads((destination / "dashboard/data/applications.json").read_text())
            self.assertEqual(payload["generated_at"], "2026-10-20T10:00:00+08:00")
            self.assertEqual(len(payload["applications"]), 15)
            self.assertIn("window.JOB_SEARCH_DATA =", (destination / "dashboard/data.js").read_text())

    def test_fictional_privacy_markers_only(self):
        fixture = json.dumps(demo.synthetic_applications()).lower()
        for marker in ("@gmail.com", "@yahoo.com", "@outlook.com", "linkedin.com", "/users/", "student pass", "resume.pdf"):
            self.assertNotIn(marker, fixture)
        self.assertTrue(re.search(r"demo-resume-\d{2}\.pdf", fixture))

    def test_gif_merger_builds_deterministic_three_frame_animation(self):
        single_frame = base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        )
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = []
            for index in range(3):
                frame = directory / f"frame-{index}.gif"
                frame.write_bytes(single_frame)
                inputs.append(frame)
            output = directory / "animated.gif"
            demo_gif.merge(inputs, output, delay=90)
            first_run = output.read_bytes()
            demo_gif.merge(inputs, output, delay=90)
            self.assertEqual(output.read_bytes(), first_run)
            self.assertTrue(first_run.startswith(b"GIF89a"))
            self.assertEqual(first_run.count(b"\x21\xf9\x04\x04"), 3)
            self.assertEqual(first_run.count(b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x80"), 3)
            self.assertTrue(first_run.endswith(b"\x3b"))
            import check_publication
            scanner = check_publication.Scanner()
            scanner.content("docs/img/demo-run.gif", first_run)
            self.assertTrue(scanner.report()["ok"])
            scanner.content("docs/img/demo-run.gif", first_run + b"trailing")
            self.assertFalse(scanner.report()["ok"])
            scanner = check_publication.Scanner()
            marker = ("PRIVATE" + "_CANARY_" + "GIF").encode()
            commented = first_run[:-1] + b"\x21\xfe" + bytes([len(marker)]) + marker + b"\0\x3b"
            scanner.content("docs/img/demo-run.gif", commented)
            self.assertIn("synthetic_canary", scanner.report()["findings"])


if __name__ == "__main__":
    unittest.main()
