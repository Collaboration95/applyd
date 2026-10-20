"""No click occurs: model the crash windows and a competing browser writer."""
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow as fixtures
import workspace as w


class SubmitIntentTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    add_release = fixtures.WorkflowTests.add_release
    save_registry = fixtures.WorkflowTests.save_registry
    job = fixtures.WorkflowTests.job
    capture = fixtures.WorkflowTests.capture
    event = fixtures.WorkflowTests.event
    ready = fixtures.WorkflowTests.ready
    record = fixtures.WorkflowTests.record
    evidence = fixtures.WorkflowTests.evidence
    def begin(self, app, url=None):
        return w.begin_submit(self.root, app, url or self.job()["listing_url"], "synthetic-session", self.now)

    def test_observed_identity_mismatch_blocks_without_claim(self):
        app = self.ready()
        with self.assertRaisesRegex(ValueError, "observed_job_mismatch"):
            self.begin(app, "https://www.linkedin.com/jobs/view/999")
        self.assertFalse((self.root / "applications/browser-claim.json").exists())

    def test_intent_survives_crash_and_cannot_retry(self):
        app = self.ready()
        self.assertTrue(self.begin(app)["click_authorized"])
        self.assertEqual(self.record(app)["status"], "submission_uncertain")
        with self.assertRaises(ValueError):
            self.begin(app)
        self.assertTrue(w.recover_submit(self.root, self.now)["requires_reconciliation"])
        self.event(app, "failed", evidence=self.evidence(), note="Synthetic inspection confirms no submission")
        self.event(app, "preparing")
        self.assertFalse((self.root / "applications/browser-claim.json").exists())

    def test_confirmed_stale_ready_page_never_replays(self):
        app = self.ready()
        self.begin(app)
        self.event(app, "submitted", evidence=self.evidence())
        stale = self.record(app)
        stale["status"] = "ready"
        w.atomic_json(self.root / "applications/records" / app / "record.json", stale)
        with self.assertRaisesRegex(ValueError, "Confirmed"):
            self.begin(app)

    def test_competing_claims_have_one_winner(self):
        app = self.ready()
        def attempt(_):
            try:
                return self.begin(app)["click_authorized"]
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)

    def test_recovery_between_claim_and_record_blocks_replay(self):
        app = self.ready()
        w.atomic_json(self.root / "applications/browser-claim.json", {"application_id": app, "attempt_id": "synthetic-intent"})
        self.assertTrue(w.recover_submit(self.root, self.now)["requires_reconciliation"])
        self.assertEqual(self.record(app)["status"], "submission_uncertain")
