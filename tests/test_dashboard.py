import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_dashboard import export_dashboard
import workspace


class DashboardExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def seed(self):
        directory = self.root / "applications/records/app-fixture"
        directory.mkdir(parents=True)
        (directory / "job-description.txt").write_text("A complete archived description.")
        (directory / "sent.pdf").write_bytes(b"saved submission fixture")
        record = {"application_id": "app-fixture", "company": "Example employer", "role": "Backend engineer",
                  "location": "Singapore", "status": "interview", "applied_by": "Intern A",
                  "listing_url": "https://www.linkedin.com/jobs/view/123456", "captured_at": "2026-10-18T15:00:00Z",
                  "status_updated_at": "2026-10-19T17:00:00Z", "job_description_path": "applications/records/app-fixture/job-description.txt",
                  "prepared": {"id": "newer-prepared", "prepared_at": "2026-10-19T01:00:00Z"},
                  "events": [{"at": "2026-10-18T15:00:00Z", "status": "discovered"},
                             {"at": "2026-10-18T17:00:00Z", "status": "submitted"},
                             {"at": "2026-10-19T17:00:00Z", "status": "interview"}],
                  "submissions": [{"id": "actually-sent-v1", "submitted_at": "2026-10-18T17:00:00Z",
                                   "exact_resume_copy": "applications/records/app-fixture/sent.pdf"}]}
        (directory / "record.json").write_text(json.dumps(record))
        return directory

    def payload(self):
        return json.loads((self.root / "dashboard/data/applications.json").read_text())

    def test_exports_exact_sent_resume_and_retained_description(self):
        self.seed()
        export_dashboard(self.root)
        app = self.payload()["applications"][0]
        self.assertEqual(app["submissions"][0]["resume"]["id"], "actually-sent-v1")
        self.assertEqual(app["submissions"][0]["resume"]["url"], "../applications/records/app-fixture/sent.pdf")
        self.assertEqual(app["prepared"]["resume"]["id"], "newer-prepared")
        self.assertEqual(app["description"], "A complete archived description.")
        self.assertEqual(app["applied_by"], "Intern A")

    def test_preserves_submission_time_without_day_buckets(self):
        self.seed()
        export_dashboard(self.root)
        app = self.payload()["applications"][0]
        self.assertEqual(app["submissions"][0]["at"], "2026-10-18T17:00:00Z")
        self.assertNotIn("activity_days", app)
        self.assertFalse((self.root / "dashboard/data/days").exists())

    def test_capture_refreshes_static_export_without_a_server(self):
        workspace.capture(self.root, {"company": "Example", "role": "Engineer", "location": "Singapore",
                          "listing_url": "https://example.com/job/1", "job_description": "Complete description"},
                          datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.assertEqual(len(self.payload()["applications"]), 1)
        self.assertTrue((self.root / "dashboard/data.js").read_text().startswith("window.JOB_SEARCH_DATA = "))

    def test_empty_export_stays_empty(self):
        export_dashboard(self.root)
        self.assertEqual(self.payload()["applications"], [])
        self.assertFalse((self.root / "dashboard/data/index.json").exists())
