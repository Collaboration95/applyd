import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from outcomes import apply_outcome, overdue


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "application_id": "synthetic-application",
            "status": "submitted",
            "submitted_at": "2026-10-01T10:00:00+08:00",
            "submissions": [{"id": "sent-v1", "submitted_at": "2026-10-01T10:00:00+08:00"}],
            "events": [{"at": "2026-10-01T10:00:00+08:00", "status": "submitted"}],
        }

    def test_outcome_keeps_original_submission_and_grouping_time(self):
        original = copy.deepcopy(self.record)
        result = apply_outcome(self.record, "interview", "2026-10-10T09:00:00+08:00",
                               "2026-10-20T12:00:00+08:00")
        self.assertEqual(result["submitted_at"], original["submitted_at"])
        self.assertEqual(result["submissions"], original["submissions"])
        self.assertEqual(result["events"], original["events"])
        self.assertEqual(self.record, original)
        self.assertEqual(result["outcome"], "interview")
        self.assertEqual(result["latest_outcome_at"], "2026-10-10T09:00:00+08:00")

    def test_delayed_follow_up_is_separate_from_event_time(self):
        result = apply_outcome(self.record, "assessment", "2026-10-02T15:00:00Z",
                               "2026-10-20T12:00:00+08:00",
                               follow_up_at="2026-10-25T12:00:00+08:00")
        self.assertEqual(result["latest_outcome_at"], "2026-10-02T15:00:00Z")
        self.assertEqual(result["follow_up_at"], "2026-10-25T12:00:00+08:00")
        self.assertFalse(overdue(result, "2026-10-25T11:59:59+08:00"))
        self.assertTrue(overdue(result, "2026-10-25T12:00:00+08:00"))

    def test_follow_up_days_schedule_from_event_occurrence(self):
        result = apply_outcome(self.record, "interview", "2026-10-10T09:00:00+08:00",
                               "2026-10-20T12:00:00+08:00", follow_up_days=5)
        self.assertEqual(result["follow_up_at"], "2026-10-15T09:00:00+08:00")
        self.assertTrue(overdue(result, "2026-10-15T09:00:00+08:00"))

    def test_no_response_uses_original_submission_and_default_days(self):
        self.assertFalse(overdue(self.record, "2026-10-15T09:59:59+08:00"))
        self.assertTrue(overdue(self.record, "2026-10-15T10:00:00+08:00"))

    def test_later_recorded_older_event_does_not_replace_latest_outcome(self):
        first = apply_outcome(self.record, "interview", "2026-10-10T09:00:00+08:00",
                              "2026-10-10T10:00:00+08:00")
        second = apply_outcome(first, "acknowledged", "2026-10-08T09:00:00+08:00",
                               "2026-10-20T12:00:00+08:00")
        self.assertEqual(second["outcome"], "interview")
        self.assertEqual(second["latest_outcome_at"], "2026-10-10T09:00:00+08:00")

    def test_duplicate_event_is_idempotent(self):
        args = (self.record, "rejected", "2026-10-12T10:00:00Z", "2026-10-13T10:00:00Z")
        once = apply_outcome(*args, note="Employer update")
        twice = apply_outcome(once, "rejected", "2026-10-12T10:00:00Z", "2026-10-13T10:00:00Z",
                              note="Employer update")
        self.assertEqual(len(twice["outcome_events"]), 1)
        self.assertEqual(twice, once)

    def test_requires_confirmed_submission_and_offsets(self):
        with self.assertRaisesRegex(ValueError, "confirmed original submission"):
            apply_outcome({"status": "submitted"}, "offer", "2026-10-02T10:00:00Z",
                          "2026-10-02T11:00:00Z")
        for stamp in ("2026-10-02T10:00:00", "not-a-time"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                apply_outcome(self.record, "offer", stamp, "2026-10-02T11:00:00Z")


if __name__ == "__main__":
    unittest.main()
