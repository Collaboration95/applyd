"""Allowed destinations are explicit and never inferred from a company name."""
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scope_guard
import settings
import workspace as w


class ScopeTests(unittest.TestCase):
    def test_default_is_linkedin_and_explicit_ats_allow_is_bounded(self):
        policy = settings._validate({})
        self.assertTrue(scope_guard.allowed("https://www.linkedin.com/jobs/view/1", policy))
        self.assertFalse(scope_guard.allowed("https://careers.example.org/job/1", policy))
        policy["external_hosts"]["allow"] = ["careers.example.org"]
        self.assertTrue(scope_guard.allowed("https://careers.example.org/job/1", policy))
        self.assertFalse(scope_guard.allowed("https://careers.example.org.evil.test/job/1", policy))
        policy["easy_apply_only"] = True
        self.assertFalse(scope_guard.allowed("https://careers.example.org/job/1", policy))

    def test_off_scope_is_durable_and_other_fact_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(timezone.utc)
            job = {"source": "linkedin_job_page", "company": "Fictional Co", "role": "Engineer", "location": "Example City", "listing_url": "https://www.linkedin.com/jobs/view/990", "job_description": "Synthetic full text"}
            app = w.capture(root, job, now)["application_id"]
            record = w.records(root)[0]
            with self.assertRaisesRegex(ValueError, "off_scope"):
                w.check_scope(root, record, "https://outside.example.org/job", now)
            self.assertEqual(w.records(root)[0]["stop_reason"], "off_scope")
            with self.assertRaisesRegex(ValueError, "source"):
                w.capture(root, {**job, "source": "company_website"}, now)
