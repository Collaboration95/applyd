"""Synthetic CLI round trips for answer review, exports and settings changes."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow as fixtures
import workspace as w
import settings
import answers
from export_recovery import recover


class DashboardIntegrationTests(unittest.TestCase):
    def test_deferred_export_matches_default_application_data(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            roots = [Path(left), Path(right)]
            job = {"source": "linkedin_job_page", "company": "Synthetic Co", "role": "Engineer", "location": "Example City", "listing_url": "https://www.linkedin.com/jobs/view/9199", "job_description": "Synthetic full description"}
            now = datetime(2026, 10, 20, tzinfo=timezone.utc)
            w.capture(roots[0], job, now)
            result = w.capture(roots[1], job, now, deferred=True)
            self.assertIsNone(result["export"]["ok"])
            self.assertFalse((roots[1] / "dashboard/data.js").exists())
            self.assertTrue(recover(roots[1], w.export_csv)["ok"])
            payloads = [w.read(root / "dashboard/data/applications.json") for root in roots]
            for payload in payloads:
                payload.pop("generated_at")
            self.assertEqual(payloads[0], payloads[1])
            self.assertEqual((roots[0] / "applications/log.csv").read_bytes(), (roots[1] / "applications/log.csv").read_bytes())

    def test_export_failure_keeps_capture_and_recovery_never_replays_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {"source": "linkedin_job_page", "company": "Synthetic Co", "role": "Engineer", "location": "Example City", "listing_url": "https://www.linkedin.com/jobs/view/9101", "job_description": "Synthetic full description"}
            with patch.object(w, "export_dashboard", side_effect=OSError("synthetic export failure")):
                result = w.capture(root, job, datetime.now(timezone.utc))
            self.assertTrue(result["mutation"]["ok"])
            self.assertFalse(result["export"]["ok"])
            record = root / "applications/records" / result["application_id"] / "record.json"
            before = record.read_bytes()
            self.assertTrue(recover(root, w.export_csv)["ok"])
            self.assertTrue(recover(root, w.export_csv)["ok"])
            self.assertEqual(record.read_bytes(), before)
            with patch.object(w, "records", wraps=w.records) as scan:
                w.export_csv(root)
                self.assertEqual(scan.call_count, 1)
            with (root / "applications/log.csv").open() as stream:
                csv_rows = list(csv.DictReader(stream))
            payload = w.read(root / "dashboard/data/applications.json")
            self.assertEqual(len(csv_rows), len(payload["applications"]))
            w.export_csv(root, snapshot=[])
            self.assertEqual(w.read(root / "dashboard/data/applications.json")["applications"], [])

    def test_settings_import_rejects_stale_or_invalid_batches_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = settings.load_settings(root)
            change = {"schema_version": 1, "base_hash": settings.settings_hash(current), "changes": {"mode": "dry_run"}}
            self.assertTrue(w.import_settings(root, change)["persisted"])
            before = (root / "settings.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "Stale"):
                w.import_settings(root, change)
            bad = {**change, "base_hash": settings.settings_hash(settings.load_settings(root)), "changes": {"mode": "invalid"}}
            with self.assertRaises(ValueError):
                w.import_settings(root, bad)
            self.assertEqual((root / "settings.json").read_bytes(), before)

    def test_answer_once_cli_import_requeues_only_fully_resolved_jobs(self):
        fixture = fixtures.WorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        submitted = fixture.ready()
        fixture.event(submitted, "submitted", evidence=fixture.evidence())
        original = fixture.record(submitted)["submitted_at"]
        w.atomic_json(root / "settings.json", {"resume_scope": "backend"})
        ids = []
        for index in (1, 2):
            data = fixture.job(company=f"Synthetic Employer {index}", platform_job_id=str(9200 + index), listing_url=f"https://www.linkedin.com/jobs/view/{9200 + index}")
            app = w.capture(root, data, fixture.now)["application_id"]
            questions = [{"question": "Shared synthetic qualification?"}]
            if index == 2:
                questions.append({"question": "Extra synthetic qualification?"})
            w.prepare_answers(root, app, questions, fixture.now)
            ids.append(app)
        bank = w.read(root / "profile/answers.json")
        change = {"schema_version": 1, "import_id": "dashboard-synthetic-one", "base_version": answers.bank_version(bank), "changes": [{"change_id": "shared-synthetic-one", "answer": {"id": "shared-synthetic", "value": "Confirmed", "scope": {}, "type": "text", "aliases": ["Shared synthetic qualification?"], "source": "Synthetic user review", "confirmed_at": fixture.now.isoformat()}}]}
        path = root / "changes.json"
        w.atomic_json(path, change)
        command = [sys.executable, str(Path(w.__file__)), "--workspace", str(root), "import-answers", "--file", str(path)]
        first = subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertTrue(json.loads(first.stdout)["persisted"])
        subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertEqual(fixture.record(ids[0])["status"], "preparing")
        self.assertEqual(fixture.record(ids[1])["status"], "awaiting_answer")
        self.assertEqual(fixture.record(submitted)["submitted_at"], original)
        payload = w.read(root / "dashboard/data/applications.json")
        self.assertEqual(payload["answer_unlock_count"], 1)
        self.assertTrue(payload["answer_receipts"])
