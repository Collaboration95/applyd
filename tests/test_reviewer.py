import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reviewer


def frozen_hash(answers):
    payload = (json.dumps(answers, indent=2, ensure_ascii=False) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.answers = [
            {"occurrence_id": "years-python-1", "question": "Years of Python experience?",
             "type": "number", "answer": 1},
            {"occurrence_id": "authorized-1", "question": "Legally authorized to work?",
             "type": "single_select", "answer": "Yes"},
        ]
        self.prepared = {
            "attempt_id": "attempt-synthetic-01",
            "answers_sha256": frozen_hash(self.answers),
            "sha256": "a" * 64,
        }
        self.visible = {
            "attempt_id": self.prepared["attempt_id"],
            "resume_sha256": self.prepared["sha256"],
            "fields": [
                {"id": "years-python-1", "question": "Years of Python experience?",
                 "type": "number", "value": 1},
                {"id": "authorized-1", "question": "Legally authorized to work?",
                 "type": "single_select", "value": "Yes"},
            ],
        }

    def test_matching_snapshot_passes_and_binds_inputs(self):
        verdict = reviewer.review(self.prepared, self.answers, self.visible)
        self.assertTrue(verdict["passed"])
        self.assertIsNone(verdict["stop_reason"])
        self.assertEqual(verdict["binding"], {
            "answers_sha256": self.prepared["answers_sha256"],
            "resume_sha256": self.prepared["sha256"],
            "attempt_id": self.prepared["attempt_id"],
        })
        self.assertEqual(reviewer.validate_verdict(verdict, self.prepared,
                                                   self.answers, self.visible),
                         {"valid": True, "passed": True, "errors": []})

    def test_wrong_numeric_radio_and_resume_hash_block_with_private_diff(self):
        visible = json.loads(json.dumps(self.visible))
        visible["fields"][0]["value"] = 2
        visible["fields"][1]["value"] = "No"
        visible["resume_sha256"] = "b" * 64
        verdict = reviewer.review(self.prepared, self.answers, visible)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["stop_reason"], "review_mismatch")
        by_code = {item["code"]: item for item in verdict["differences"]}
        self.assertEqual(by_code["field_value_mismatch"]["expected"], 1)
        self.assertEqual(by_code["field_value_mismatch"]["actual"], 2)
        self.assertEqual(by_code["resume_hash_mismatch"]["actual"], "b" * 64)
        self.assertTrue(reviewer.validate_verdict(verdict, self.prepared,
                                                  self.answers, visible)["valid"])

    def test_missing_duplicate_unknown_and_identity_mismatches_block(self):
        visible = json.loads(json.dumps(self.visible))
        visible["attempt_id"] = "stale-attempt"
        visible["fields"] = [visible["fields"][0], visible["fields"][0],
                              {"id": "unknown", "question": "Unexpected?",
                               "type": "text", "value": "x"}]
        codes = {item["code"] for item in reviewer.review(
            self.prepared, self.answers, visible)["differences"]}
        self.assertTrue({"attempt_mismatch", "duplicate_visible_field",
                         "unknown_visible_field", "missing_visible_field"} <= codes)

    def test_question_and_type_must_match_exactly(self):
        visible = json.loads(json.dumps(self.visible))
        visible["fields"][0]["question"] = "Years of Python experience? "
        visible["fields"][0]["type"] = "text"
        codes = [item["code"] for item in reviewer.review(
            self.prepared, self.answers, visible)["differences"]]
        self.assertIn("field_question_mismatch", codes)
        self.assertIn("field_type_mismatch", codes)

    def test_stale_verdict_fails_validation_against_changed_snapshot(self):
        verdict = reviewer.review(self.prepared, self.answers, self.visible)
        changed = json.loads(json.dumps(self.visible))
        changed["fields"][0]["value"] = 3
        result = reviewer.validate_verdict(verdict, self.prepared,
                                           self.answers, changed)
        self.assertFalse(result["valid"])
        self.assertFalse(result["passed"])

    def test_mutated_frozen_answers_fail_hash_binding(self):
        changed_answers = json.loads(json.dumps(self.answers))
        changed_answers[0]["answer"] = 2
        verdict = reviewer.review(self.prepared, changed_answers, self.visible)
        self.assertFalse(verdict["passed"])
        self.assertIn("frozen_answers_hash_mismatch",
                      {item["code"] for item in verdict["differences"]})


if __name__ == "__main__":
    unittest.main()
