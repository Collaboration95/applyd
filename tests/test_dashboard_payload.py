import copy
import json
import sys
import subprocess
from unittest import mock
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import answers
from dashboard_payload import enrich_payload


class DashboardPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "profile").mkdir()
        self.record = {"application_id": "app-synthetic", "company": "Synthetic Employer", "role": "Synthetic Analyst",
                       "country": "SG", "listing_url": "https://jobs.example.test/role/1", "status": "awaiting_answer",
                       "blocked_on_answer_ids": [], "status_updated_at": "2026-10-20T09:00:00+08:00"}
        record_dir = self.root / "applications/records/app-synthetic"
        record_dir.mkdir(parents=True)
        (record_dir / "record.json").write_text(json.dumps(self.record))
        (self.root / "profile/answers.json").write_text(json.dumps({"schema_version": 1, "answers": []}))
        prepared = answers.prepare_answers(self.root, self.record, [{"question": "Synthetic qualification?", "type": "text", "scope": {}}], "2026-10-20T10:00:00+08:00")
        self.occurrence = prepared["answers"][0]
        self.bank = json.loads((self.root / "profile/answers.json").read_text())

    def payload(self):
        app = {"id": "app-synthetic", "company": "Synthetic Employer", "title": "Synthetic Analyst", "status": "awaiting_answer", "updated_at": "2026-10-20T01:00:00Z", "submissions": []}
        return {"timezone": "UTC", "generated_at": "2026-10-20T10:00:00Z", "applications": [app]}

    def test_enrichment_uses_snapshot_and_does_not_write_workspace(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        records = [copy.deepcopy(self.record)]
        original_payload = self.payload()
        enriched = enrich_payload(self.root, records, original_payload, datetime(2026, 10, 20, 10, tzinfo=timezone.utc))
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(records[0]["status"], "awaiting_answer")
        self.assertNotIn("attention", original_payload)
        self.assertNotIn("record_url", original_payload["applications"][0])
        self.assertEqual(enriched["timezone"], "UTC")
        self.assertEqual(enriched["inbox"]["questions"][0]["occurrences"][0]["question"], "Synthetic qualification?")
        self.assertEqual(enriched["attention"][0]["reason"], "Waiting for 1 answer.")
        self.assertEqual(enriched["attention"][0]["record_url"], "../applications/records/app-synthetic/record.json")

    def test_core_import_roundtrip_exposes_receipt_and_refreshed_bank_version(self):
        version = answers.bank_version(self.bank)
        change = {"schema_version": 1, "import_id": "synthetic-import-1", "base_version": version,
                  "changes": [{"change_id": "synthetic-change-1", "answer": {
                      "id": "synthetic-qualification", "version": 1, "value": "Confirmed", "scope": {},
                      "type": "text", "aliases": ["Synthetic qualification?"], "source": "Synthetic user review",
                      "confirmed_at": "2026-10-20T10:00:00+08:00"}}]}
        receipt = answers.import_answers(self.root, change, "2026-10-20T10:01:00+08:00")
        self.assertTrue(receipt["persisted"])
        enriched = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:02:00+08:00")
        self.assertEqual(enriched["bank_version"], receipt["bank_version"])
        self.assertEqual(enriched["answer_receipts"][0]["import_id"], "synthetic-import-1")
        self.assertEqual(enriched["answer_receipts"][0]["bank_version"], enriched["bank_version"])
        self.assertEqual(enriched["answer_sources"][0]["source"], "Synthetic user review")
        self.assertEqual(enriched["inbox"]["questions"][0]["status"], "resolved")
        self.assertEqual(enriched["inbox"]["questions"][0]["question_id"], self.occurrence["question_id"])
        self.assertEqual(enriched["inbox"]["questions"][0]["occurrences"][0]["occurrence_id"], self.occurrence["occurrence_id"])
        self.assertEqual(enriched["answer_unlock_count"], 1)
        self.assertEqual(enriched["applications"][0]["status"], "awaiting_answer")
        self.assertIn("execution checks pending", enriched["attention"][0]["reason"])

    def browser_batch(self, payload, values):
        process = subprocess.run(["node", str(ROOT / "tests/control-plane.test.cjs"), "--batch"],
                                 input=json.dumps({"payload": payload, "values": values}),
                                 capture_output=True, text=True, check=True)
        return json.loads(process.stdout)

    def test_browser_batch_roundtrip_typed_options_defaults_and_revised_source(self):
        questions = [
            {"question": "Typed single?", "type": "single_select", "options": [True, 1, "one"], "scope": {}},
            {"question": "Typed multiple?", "type": "multi_select", "options": [False, 2, "two"], "scope": {}},
            {"question": "Python years?", "type": "number", "units": "years", "kind": "experience_duration", "skill": "Python", "scope": {}},
        ]
        # An unknown authorized default is replaced by an explicitly reviewed saved answer.
        bank = {"schema_version": 1, "documented_skills": ["Python"], "answers": [{
            "id": "python-years", "version": 1, "value": None, "type": "number", "units": "years",
            "kind": "experience_duration", "skill": "Python", "scope": {}, "basis": "authorized_default",
            "authorized": True, "aliases": ["Python years?"], "confirmed_at": "2026-10-20T09:00:00+08:00", "source": "Synthetic authorization"}]}
        (self.root / "profile/answers.json").write_text(json.dumps(bank))
        prepared = answers.prepare_answers(self.root, self.record, questions, "2026-10-20T10:00:00+08:00")
        self.record["blocked_on_answer_ids"] = prepared["blocked_on_answer_ids"]
        enriched = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:00:00+08:00")
        ids = [group["question_id"] for group in enriched["inbox"]["questions"]]
        wanted = {"Typed single?": True, "Typed multiple?": [False, 2, "two"], "Python years?": 1}
        values = [wanted[group["occurrences"][0]["question"]] for group in enriched["inbox"]["questions"]]
        batch = self.browser_batch(enriched, values)
        receipt = answers.import_answers(self.root, batch, "2026-10-20T10:01:00+08:00")
        self.assertEqual(len(receipt["applied_change_ids"]), 3)
        self.assertTrue(answers.import_answers(self.root, batch, "2026-10-20T10:01:00+08:00")["already_imported"])
        refreshed = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:02:00+08:00")
        self.assertTrue(all(q["status"] == "resolved" for q in refreshed["inbox"]["questions"]))
        self.assertEqual(ids, [q["question_id"] for q in refreshed["inbox"]["questions"]])
        source = next(s for s in refreshed["answer_sources"] if s["id"] == "python-years")
        self.assertEqual(source["basis"], "saved_answer")
        self.assertNotIn("authorized", source)
        self.assertEqual(source["version"], 2)
        revised_values = [{"Typed single?": "one", "Typed multiple?": [False, "two"], "Python years?": 2}[q["occurrences"][0]["question"]]
                          for q in refreshed["inbox"]["questions"]]
        second = self.browser_batch(refreshed, revised_values)
        self.assertTrue(set(c["change_id"] for c in batch["changes"]).isdisjoint(c["change_id"] for c in second["changes"]))
        second_receipt = answers.import_answers(self.root, second, "2026-10-20T10:03:00+08:00")
        self.assertEqual(len(second_receipt["applied_change_ids"]), 3)
        final = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:04:00+08:00")
        for group in final["inbox"]["questions"]:
            occurrence = group["occurrences"][0]
            self.assertEqual(json.dumps(occurrence["answer"]), json.dumps({"Typed single?": "one", "Typed multiple?": [False, "two"], "Python years?": 2}[occurrence["question"]]))
            self.assertEqual(occurrence["resolution"], "resolved")

    def test_current_bank_expiry_and_missing_record_never_resolve_from_historical_occurrence(self):
        batch = self.browser_batch(enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:00:00+08:00"), ["Yes"])
        batch["changes"][0]["answer"]["valid_until"] = "2026-10-20T11:00:00+08:00"
        answers.import_answers(self.root, batch, "2026-10-20T10:01:00+08:00")
        expired = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T12:00:00+08:00")
        self.assertEqual(expired["inbox"]["questions"][0]["status"], "unresolved")
        missing = enrich_payload(self.root, [], self.payload(), "2026-10-20T10:02:00+08:00")
        self.assertEqual(missing["inbox"]["questions"][0]["status"], "unresolved")
        self.assertEqual(missing["inbox"]["questions"][0]["occurrences"][0]["resolution"], "application_context_unavailable")

    def test_attention_all_nonterminal_includes_fresh_queued_and_stale_distinctions(self):
        records = [{**self.record, "application_id": f"app-{status}", "status": status, "status_updated_at": "2026-10-20T10:00:00+08:00"}
                   for status in ["discovered", "preparing", "ready", "failed", "blocked", "submission_uncertain", "submitted", "skipped"]]
        records.append({**self.record, "application_id": "app-stale", "status": "preparing", "status_updated_at": "2026-10-18T10:00:00+08:00"})
        enriched = enrich_payload(self.root, records, self.payload(), "2026-10-20T11:00:00+08:00")
        attention = {a["application_id"]: a for a in enriched["attention"]}
        self.assertEqual(len(attention), 7)
        self.assertIn("Queued", attention["app-discovered"]["reason"])
        self.assertIn("queued", attention["app-ready"]["reason"])
        self.assertNotIn("24 hours", attention["app-preparing"]["reason"])
        self.assertIn("24 hours", attention["app-stale"]["reason"])

    def test_bank_and_inbox_are_read_once_and_export_is_byte_for_byte_readonly(self):
        import dashboard_payload
        with mock.patch.object(dashboard_payload, "_read_json", wraps=dashboard_payload._read_json) as read:
            before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
            enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:02:00+08:00")
            self.assertEqual(read.call_count, 2)
            self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_unknown_record_blocker_is_never_silently_removed_by_readonly_resolution(self):
        exported = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:00:00+08:00")
        batch = self.browser_batch(exported, ["Yes"])
        answers.import_answers(self.root, batch, "2026-10-20T10:01:00+08:00")
        record = {**self.record, "blocked_on_answer_ids": ["q-not-captured"]}
        current = enrich_payload(self.root, [record], self.payload(), "2026-10-20T10:02:00+08:00")
        self.assertEqual(current["answer_unlock_count"], 0)
        self.assertIn("q-not-captured", current["applications"][0]["blocked_on_answer_ids"])
        self.assertEqual(current["inbox"]["questions"][0]["status"], "resolved")

    def test_core_requeue_canonical_blocker_is_recognized_after_revised_answer(self):
        exported = enrich_payload(self.root, [self.record], self.payload(), "2026-10-20T10:00:00+08:00")
        batch = self.browser_batch(exported, ["Yes"])
        answers.import_answers(self.root, batch, "2026-10-20T10:01:00+08:00")
        current_row = answers._rows(json.loads((self.root / "profile/answers.json").read_text()), self.record,
                                    [{"question": "Synthetic qualification?", "type": "text", "scope": {}}],
                                    datetime.fromisoformat("2026-10-20T10:02:00+08:00"))[0]
        record = {**self.record, "blocked_on_answer_ids": [current_row["question_id"]]}
        current = enrich_payload(self.root, [record], self.payload(), "2026-10-20T10:02:00+08:00")
        self.assertEqual(current["answer_unlock_count"], 1)
        self.assertEqual(current["applications"][0]["blocked_on_answer_ids"], [])


if __name__ == "__main__":
    unittest.main()
