"""Batch quotas and retry proposals use synthetic in-memory records only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import batches as b


EARLY = "2026-10-01T09:00:00+08:00"
LATER = "2026-10-02T09:00:00+08:00"


def job(identity, **values):
    return {"application_id": identity, "status": "preparing", "company": "Example Co",
            "role": "Engineer " + identity, "location": "Example City", "platform": "linkedin",
            "listing_url": "https://example.invalid/jobs/" + identity,
            "submissions": [], **values}


def confirmation(batch="new", category="job", at=LATER, **values):
    return {"batch_id": batch, "category": category, "status": "submitted",
            "submitted_at": at, "confirmation_path": "synthetic/confirmation.txt", **values}


class BatchTests(unittest.TestCase):
    def test_prior_batch_and_duplicate_attempt_cannot_increment_new_batch(self):
        old = job("original", status="interview", batch_id="new", category="internship",
                  submitted_at=EARLY, submissions=[confirmation("old", at=EARLY), confirmation()])
        duplicate = job("copy", listing_url=old["listing_url"], status="submitted",
                        batch_id="new", submissions=[confirmation()])
        new = b.batch_progress([old, duplicate], "new", 2)
        self.assertEqual(new["confirmed"], 0)
        self.assertEqual(new["remaining"], 2)
        self.assertEqual(new["exclusions"]["confirmed"], ["copy", "original"])
        self.assertEqual(b.batch_progress([duplicate, old], "old", 1)["confirmed"], 1)

    def test_new_confirmation_counts_once_and_uses_frozen_category(self):
        record = job("one", status="rejected", batch_id="other", category="job",
                     submissions=[confirmation(category="internship"), confirmation(category="internship")])
        record["prepared"] = {"batch_id": "other", "category": "job"}
        result = b.batch_progress([record], "new", 1, {"internship": 1, "job": 0})
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(result["confirmed_application_ids"], ["one"])
        self.assertEqual(result["categories"]["internship"]["confirmed"], 1)
        self.assertEqual(result["categories"]["job"]["confirmed"], 0)
        self.assertTrue(result["complete"])

    def test_missing_frozen_context_never_inherits_mutable_batch(self):
        saved = confirmation()
        saved.pop("batch_id")
        record = job("one", status="submitted", batch_id="new", submissions=[saved],
                     prepared={"batch_id": "new"})
        result = b.batch_progress([record], "new", 1)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["exclusions"]["confirmed"], ["one"])

    def test_legacy_confirmation_excludes_without_assigning_a_batch(self):
        legacy = job("legacy", batch_id="new", submitted_at=EARLY,
                     confirmation_path="synthetic/old.txt", submissions=[confirmation()])
        self.assertEqual(b.batch_progress([legacy], "new", 1)["confirmed"], 0)

    def test_confirmation_requires_timestamp_and_evidence_not_status(self):
        records = [job("status-only", status="submitted"),
                   job("timestamp", submissions=[confirmation(confirmation_path=None)]),
                   job("evidence", submissions=[confirmation(submitted_at=None)]),
                   job("uncertain", status="submission_uncertain"),
                   job("dry-run", status="dry_run_complete")]
        result = b.batch_progress(records, "new", 1)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["exclusions"]["confirmed"], [])
        self.assertEqual(result["exclusions"]["submission_uncertain"], ["uncertain"])

    def test_shared_identities_dedupe_independently(self):
        variants = [
            ({"application_id": "same"}, {"application_id": "same"}),
            ({"platform": "LinkedIn", "platform_job_id": "42"},
             {"platform": "linkedin", "platform_job_id": "42"}),
            ({"company": " EXAMPLE ", "employer_requisition_id": "Req-1"},
             {"company": "example", "employer_requisition_id": "req-1"}),
            ({"listing_url": "https://example.invalid/jobs/1?utm_source=demo#top"},
             {"listing_url": "https://example.invalid/jobs/1"}),
            ({"listing_url": "https://sg.linkedin.com/jobs/view/engineer-42/?trk=test"},
             {"listing_url": "https://www.linkedin.com/jobs/search/?currentJobId=42"}),
            ({"canonical_listing_url": "https://www.linkedin.com/jobs/view/42"},
             {"platform": "LinkedIn", "platform_job_id": "42"}),
            ({"company": "Example", "role": "Software Engineer", "location": "City"},
             {"company": "EXAMPLE", "role": "software  engineer", "location": "city"}),
        ]
        for left, right in variants:
            with self.subTest(left=left):
                records = [{"application_id": "one", **left, "submissions": [confirmation()]},
                           {"application_id": "two", **right, "submissions": [confirmation()]}]
                self.assertEqual(b.batch_progress(records, "new", 2)["confirmed"], 1)

    def test_unconfirmed_cross_post_bridge_connects_confirmed_groups(self):
        first = {"application_id": "first", "company": "Example", "employer_requisition_id": "A",
                 "submissions": [confirmation("old", at=EARLY)]}
        bridge = {"application_id": "bridge", "company": "Example", "employer_requisition_id": "A",
                  "listing_url": "https://example.invalid/jobs/1", "status": "awaiting_answer"}
        later = {"application_id": "later", "listing_url": bridge["listing_url"],
                 "submissions": [confirmation()]}
        for records in ([first, later, bridge], [bridge, later, first]):
            self.assertEqual(b.batch_progress(records, "new", 1)["confirmed"], 0)
            self.assertEqual(b.batch_progress(records, "old", 1)["confirmed"], 1)

    def test_ambiguous_original_ownership_does_not_earn_either_batch(self):
        record = job("one", submissions=[confirmation("old"), confirmation("new")])
        self.assertEqual(b.batch_progress([record], "new", 1)["confirmed"], 0)
        self.assertEqual(b.batch_progress([record], "old", 1)["confirmed"], 0)

    def test_conflicting_original_categories_do_not_double_count_total(self):
        record = job("one", submissions=[confirmation(category="job"), confirmation(category="internship")])
        result = b.batch_progress([record], "new", 1, {"job": 1, "internship": 1})
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual([c["confirmed"] for c in result["categories"].values()], [0, 0])

    def test_delayed_recording_order_cannot_replace_original_ownership(self):
        record = job("one", submissions=[confirmation(recorded_at=LATER),
                                        confirmation("old", at=EARLY, recorded_at=LATER)])
        self.assertEqual(b.batch_progress([record], "new", 1)["confirmed"], 0)
        self.assertEqual(b.batch_progress([record], "old", 1)["confirmed"], 1)

    def test_category_and_total_targets_and_remaining(self):
        record = job("one", submissions=[confirmation()])
        result = b.batch_progress([record], "new", 1, {"job": 0, "internship": 1})
        self.assertEqual(result["remaining"], 0)
        self.assertFalse(result["complete"])
        self.assertEqual(result["categories"]["job"]["remaining"], 0)
        self.assertTrue(b.batch_progress([], "new", 0)["complete"])

    def test_queued_jobs_become_retryable_when_all_saved_blockers_clear(self):
        record = job("queued", status="awaiting_answer", blocked_on_answer_ids=["q-one", "q-two"],
                     events=[{"status": "awaiting_answer", "at": EARLY}])
        settings = {"retryable_statuses": ["awaiting_answer", "failed"]}
        self.assertEqual(b.retryable_application_ids([record], settings), [])
        record["blocked_on_answer_ids"] = ["q-two"]
        self.assertEqual(b.retryable_application_ids([record], settings), [])
        record["blocked_on_answer_ids"] = []
        self.assertEqual(b.retryable_application_ids([record], settings), ["queued"])
        record["status"] = "failed"
        self.assertEqual(b.retryable_application_ids([record], settings), ["queued"])
        self.assertEqual(b.retryable_application_ids([record], {"retryable_statuses": []}), [])
        self.assertEqual(b.batch_progress([record], "new", 1)["exclusions"],
                         {"confirmed": [], "submission_uncertain": []})
        record["status"] = "preparing"  # Parent's persisted answers-resolved transition.
        self.assertEqual(b.retryable_application_ids([record], {"retryable_statuses": ["preparing"]}), ["queued"])

    def test_uncertain_or_confirmed_aliases_cannot_be_retried(self):
        record = job("failed", status="failed")
        alias = job("alias", listing_url=record["listing_url"], status="submission_uncertain")
        self.assertEqual(b.retryable_application_ids([record, alias], {}), [])
        alias["status"] = "failed"
        alias["submission_intent"] = {"attempt_id": "synthetic"}
        self.assertEqual(b.retryable_application_ids([record, alias], {}), [])
        alias.pop("submission_intent")  # Parent reconciles non-submission with evidence.
        self.assertEqual(b.retryable_application_ids([record, alias], {}), ["alias", "failed"])
        alias["submissions"] = [confirmation()]
        self.assertEqual(b.retryable_application_ids([record, alias], {}), [])

    def test_invalid_retry_setting_cannot_allow_terminal_status(self):
        record = job("uncertain", status="submission_uncertain")
        self.assertEqual(b.retryable_application_ids([record], {"retryable_statuses": ["submission_uncertain"]}), [])

    def test_counts_and_retries_do_not_mutate_records(self):
        records = [job("one", submissions=[confirmation()])]
        original = deepcopy(records)
        b.batch_progress(iter(records), "new", 1)
        b.retryable_application_ids(iter(records), {})
        self.assertEqual(records, original)

    def test_invalid_inputs_fail_instead_of_silently_reporting_progress(self):
        for target in (-1, True, "1", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                b.batch_progress([], "new", target)
        for categories in ({"job": -1}, {"job": True}, {"": 1}, []):
            with self.subTest(categories=categories), self.assertRaises(ValueError):
                b.batch_progress([], "new", 1, categories)
        for record in (None, {}, {"application_id": "one", "submissions": {}},
                       job("one", submissions=[confirmation(submitted_at="2026-10-01T10:00:00")])):
            with self.subTest(record=record), self.assertRaises(ValueError):
                b.batch_progress([record], "new", 1)


class EventTimeTests(unittest.TestCase):
    def test_delayed_confirmation_keeps_original_run_boundary_metadata(self):
        prior = b.validate_event_time("2026-10-01T09:00:00+08:00")
        metadata = b.event_metadata("2026-10-01T09:45:00+08:00", "2026-10-02T12:00:00+08:00")
        self.assertEqual(metadata["at"], metadata["occurred_at"])
        self.assertLessEqual(b.validate_event_time(metadata["at"]) - prior, timedelta(hours=1))
        self.assertGreater(b.validate_event_time(metadata["recorded_at"]) - prior, timedelta(hours=1))
        submission = {**confirmation(at=metadata["occurred_at"]), **metadata}
        record = job("late", submissions=[submission])
        self.assertEqual(b.batch_progress([record], "new", 1)["confirmed"], 1)

    def test_timestamps_accept_offsets_and_aware_clocks(self):
        clock = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(b.validate_event_time(clock), clock)
        self.assertEqual(b.validate_event_time("2026-10-01T08:00:00+08:00"), clock)
        self.assertEqual(b.event_metadata(clock, "2026-10-01T00:00:00Z")["at"], clock.isoformat())

    def test_naive_invalid_or_future_event_times_are_rejected(self):
        for stamp in ("2026-10-01T10:00:00", "2026-10-01", "bad", None, 1, datetime(2026, 10, 1)):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, "UTC offset"):
                b.validate_event_time(stamp)
        with self.assertRaisesRegex(ValueError, "recorded_at"):
            b.event_metadata(EARLY, "2026-10-01T10:00:00")
        with self.assertRaisesRegex(ValueError, "later than"):
            b.event_metadata(LATER, EARLY)

    def test_context_is_frozen_copied_and_preserved_on_confirmation(self):
        snapshot = {"prepared_at": EARLY, "resume": {"id": "synthetic-resume"}}
        prepared = b.attach_attempt_context(snapshot, "new", "internship")
        self.assertNotIn("batch_id", snapshot)
        prepared["resume"]["id"] = "another-resume"
        self.assertEqual(snapshot["resume"]["id"], "synthetic-resume")
        self.assertEqual(b.attach_attempt_context(prepared, "new", "internship"), prepared)
        record = job("one", batch_id="changed", submissions=[{**confirmation(), **prepared}])
        self.assertEqual(b.batch_progress([record], "new", 1, {"internship": 1})["categories"]["internship"]["confirmed"], 1)
        with self.assertRaisesRegex(ValueError, "frozen"):
            b.attach_attempt_context(prepared, "other", "internship")
        with self.assertRaisesRegex(ValueError, "frozen"):
            b.attach_attempt_context(prepared, "new", "job")

    def test_invalid_context_is_rejected(self):
        for snapshot, batch, category in (([], "new", None), ({}, "", None), ({}, None, "job"),
                                          ({}, "new", " job"), ({"prepared_at": "2026-10-01"}, "new", None)):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                b.attach_attempt_context(snapshot, batch, category)


if __name__ == "__main__":
    unittest.main()
