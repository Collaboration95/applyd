"""Answer-loop contract tests. Every profile, job, and artifact is synthetic."""

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import answers as a


class AnswersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime(2026, 10, 20, 12, tzinfo=timezone(timedelta(hours=8)))
        self.bank = {"schema_version": 1, "answers": [], "documented_skills": ["Python", "SQL"]}
        self.record = {"application_id": "app-synthetic", "company": "Example Company",
                       "country": "SG", "listing_url": "https://example.org/jobs/1",
                       "destination_url": "https://example.org/apply/1", "status": "preparing",
                       "submissions": []}
        self.save_bank()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def read(self, path):
        return json.loads((self.root / path).read_text(encoding="utf-8"))

    def save_bank(self):
        self.write("profile/answers.json", self.bank)

    def entry(self, identity="experience", **changes):
        entry = {"id": identity, "version": 1, "value": 1, "type": "number", "units": "years",
                "kind": "experience_duration", "skill": "Python", "scope": {"country": "SG"},
                "aliases": ["Python experience?", "Years using Python?"],
                "source": "Synthetic user confirmation", "confirmed_at": (self.now - timedelta(days=1)).isoformat(),
                "valid_until": (self.now + timedelta(days=3)).isoformat(), **changes}
        if entry.get("skill") == "":
            del entry["skill"]
        return entry

    def question(self, **changes):
        return {"question": "Python experience?", "type": "number", "units": "years",
                "kind": "experience_duration", "skill": "Python", "scope": {"country": "SG"}, **changes}

    def known(self, **changes):
        self.bank["answers"] = [self.entry(**changes)]
        self.save_bank()
        return a.prepare_answers(self.root, self.record, [self.question()], self.now)

    def valid(self, result, now=None, **extra):
        return a.validate_snapshot(self.root, self.record, result["answers"], now or self.now, **extra)

    def change(self, entries, identity="import-one"):
        return {"schema_version": 1, "import_id": identity, "base_version": a.bank_version(a._load_bank(self.root)),
                "changes": [{"change_id": f"{identity}-{i}", "answer": e} for i, e in enumerate(entries)]}

    def save_record(self, record=None):
        record = record or self.record
        self.write(f"applications/records/{record['application_id']}/record.json", record)

    def bytes_under_root(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_known_answer_has_exact_provenance_and_one_bank_read(self):
        self.bank["answers"] = [self.entry()]
        self.save_bank()
        with mock.patch.object(a, "_load_bank", wraps=a._load_bank) as load:
            result = a.prepare_answers(self.root, self.record, [self.question(), self.question(question="Years using Python?")], self.now)
        self.assertEqual(load.call_count, 1)
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["answers"]), 2)
        self.assertEqual(len({x["question_id"] for x in result["answers"]}), 1)
        row = result["answers"][0]
        self.assertEqual(row["answer"], 1)
        self.assertEqual(row["source_answer_id"], "experience")
        self.assertEqual(row["source_version"], 1)
        self.assertEqual(row["source_value"], 1)
        self.assertEqual(row["source_scope"], {"country": "SG"})
        self.assertEqual(row["basis"], "saved_answer")
        self.assertTrue(self.valid(result)["valid"])

    def test_deterministic_bytes_and_ids_survive_reorder_restart(self):
        self.bank["answers"] = [self.entry()]
        self.save_bank()
        questions = [self.question(), self.question(question="Years using Python?"), {"question": "Unknown fact?"}]
        one = a.prepare_answers(self.root, self.record, questions, self.now)
        before = self.bytes_under_root()
        two = a.prepare_answers(self.root, self.record, list(reversed(questions)), self.now + timedelta(seconds=1))
        self.assertEqual(one, two)
        self.assertEqual(before, self.bytes_under_root())
        self.assertEqual(self.read(one["answers_path"]), one["answers"])

    def test_normalization_does_not_guess_punctuation_or_unapproved_synonyms(self):
        self.known()
        for wording, expected in [("  PYTHON   experience? ", True), ("Python experience", False),
                                  ("How long have you used Python?", False), ("Experience with Python?", False)]:
            with self.subTest(wording=wording):
                result = a.prepare_answers(self.root, self.record, [self.question(question=wording)], self.now)
                self.assertEqual(result["ready"], expected)

    def test_unknown_and_null_never_reach_validation_even_optional(self):
        result = self.known(value=None)
        self.assertEqual(result["status"], "awaiting_answer")
        self.assertEqual(result["answers"][0]["resolution"], "unknown_answer")
        self.assertFalse(self.valid(result)["valid"])
        result = a.prepare_answers(self.root, self.record, [{"question": "New material fact?", "required": False}], self.now)
        self.assertIsNone(result["answers"][0]["answer"])
        self.assertFalse(result["ready"])

    def test_shared_unknown_canonical_ids_and_separate_occurrences(self):
        first = a.prepare_answers(self.root, self.record, [{"question": "Unconfirmed qualification?"}], self.now)
        other = {**self.record, "application_id": "app-second", "company": "Example Two"}
        second = a.prepare_answers(self.root, other, [{"question": "Unconfirmed qualification?"}], self.now)
        self.assertEqual(first["blocked_on_answer_ids"], second["blocked_on_answer_ids"])
        self.assertNotEqual(first["answers"][0]["occurrence_id"], second["answers"][0]["occurrence_id"])
        inbox = self.read("profile/question-inbox.json")
        self.assertEqual(len(inbox["questions"]), 1)
        self.assertEqual(inbox["questions"][0]["application_ids"], ["app-second", "app-synthetic"])
        occurrence = next(o for o in inbox["questions"][0]["occurrences"] if o["application_id"] == self.record["application_id"])
        for key in ("company", "listing_url", "destination_url"):
            self.assertEqual(occurrence[key], self.record[key])
        self.assertEqual(occurrence["question"], "Unconfirmed qualification?")
        self.assertEqual(occurrence["options"], [])
        self.assertIn("record_path", occurrence)

    def test_unknown_country_and_consent_context_do_not_merge(self):
        question = {"question": "Unknown declaration?", "kind": "legal_authorization"}
        one = a.prepare_answers(self.root, self.record, [question], self.now)
        other = {**self.record, "application_id": "app-us", "country": "US"}
        two = a.prepare_answers(self.root, other, [question], self.now)
        self.assertNotEqual(one["blocked_on_answer_ids"], two["blocked_on_answer_ids"])
        question["kind"] = "consent"
        one = a.prepare_answers(self.root, self.record, [question], self.now)
        other["country"] = "SG"
        two = a.prepare_answers(self.root, other, [question], self.now)
        self.assertNotEqual(one["blocked_on_answer_ids"], two["blocked_on_answer_ids"])

    def test_duplicate_form_fields_need_ids_and_are_not_collapsed(self):
        with self.assertRaisesRegex(ValueError, "field_id"):
            a.prepare_answers(self.root, self.record, [self.question(), self.question()], self.now)
        result = a.prepare_answers(self.root, self.record, [self.question(field_id="one"), self.question(field_id="two")], self.now)
        self.assertEqual(len({r["occurrence_id"] for r in result["answers"]}), 2)

    def test_type_units_country_and_kind_do_not_cross_match(self):
        self.known()
        for patch in [{"type": "text"}, {"units": "months"}, {"scope": {"country": "US"}}, {"kind": "fact"}]:
            with self.subTest(patch=patch):
                result = a.prepare_answers(self.root, self.record, [self.question(**patch)], self.now)
                self.assertFalse(result["ready"])

    def test_sensitive_source_cannot_be_global_or_guess_country_from_location(self):
        entry = self.entry("legal", type="boolean", units=None, skill="", value=True,
                           kind="legal_authorization", scope={}, aliases=["Legally authorized?"])
        question = {"question": "Legally authorized?", "type": "boolean", "kind": "legal_authorization"}
        self.bank["answers"] = [entry]
        self.save_bank()
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])
        entry["scope"] = {"country": "SG"}
        self.save_bank()
        record = {k: v for k, v in self.record.items() if k != "country"}
        record["location"] = "Singapore"
        self.assertFalse(a.prepare_answers(self.root, record, [question], self.now)["ready"])

    def test_company_and_notice_scoped_consent_is_reusable_only_within_both(self):
        self.bank["answers"] = [self.entry("consent", type="boolean", units=None, skill="", value=True,
            kind="consent", scope={"company": "Example Company", "consent_id": "notice-one"},
            aliases=["Accept this privacy notice?"])]
        self.save_bank()
        question = {"question": "Accept this privacy notice?", "type": "boolean", "kind": "consent",
                    "scope": {"consent_id": "notice-one"}}
        self.assertTrue(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])
        other = {**self.record, "company": "Example Other", "application_id": "app-other"}
        self.assertFalse(a.prepare_answers(self.root, other, [question], self.now)["ready"])
        question["scope"]["consent_id"] = "notice-two"
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])

    def test_currency_period_and_options_are_part_of_question_identity(self):
        variants = [
            {"question": "Expected salary?", "units": {"currency": "SGD", "period": "month"}},
            {"question": "Expected salary?", "units": {"currency": "USD", "period": "month"}},
            {"question": "Expected salary?", "units": {"currency": "SGD", "period": "year"}},
        ]
        result = a.prepare_answers(self.root, self.record, variants, self.now)
        self.assertEqual(len(result["blocked_on_answer_ids"]), 3)

    def test_ambiguous_alias_is_not_resolved_by_record_order(self):
        self.bank["answers"] = [self.entry(), self.entry("different", value=2)]
        self.save_bank()
        result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
        self.assertEqual(result["answers"][0]["resolution"], "ambiguous_alias")
        self.assertIsNone(result["answers"][0]["answer"])

    def test_documented_default_and_explicit_override(self):
        default = self.entry("conservative", basis="authorized_default", authorized=True)
        self.bank["answers"] = [default]
        self.save_bank()
        result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
        self.assertEqual(result["answers"][0]["basis"], "authorized_default")
        for value in (2, None):
            self.bank["answers"] = [default, self.entry(value=value)]
            self.save_bank()
            result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
            self.assertEqual(result["answers"][0]["source_answer_id"], "experience")
            self.assertEqual(result["answers"][0]["answer"], value)

    def test_no_implicit_skill_defaults_or_unauthorized_defaults(self):
        for patch in [{"authorized": False}, {"skill": "Unlisted", "aliases": ["Python experience?"]}]:
            with self.subTest(patch=patch):
                self.bank["answers"] = [self.entry(basis="authorized_default", authorized=True, **({} if "authorized" in patch else patch))]
                self.bank["answers"][0].update(patch)
                self.save_bank()
                result = a.prepare_answers(self.root, self.record, [self.question(skill=patch.get("skill", "Python"))], self.now)
                self.assertFalse(result["ready"])
        self.bank["answers"] = []
        self.save_bank()
        self.assertFalse(a.prepare_answers(self.root, self.record, [self.question()], self.now)["ready"])

    def test_expired_or_malformed_explicit_source_does_not_fall_back(self):
        default = self.entry("default", basis="authorized_default", authorized=True)
        for patch in [{"valid_until": self.now.isoformat()}, {"source": None}, {"authorized": False, "basis": "saved_answer", "valid_until": "bad"}]:
            self.bank["answers"] = [default, self.entry(**patch)]
            self.save_bank()
            result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
            self.assertFalse(result["ready"])
            self.assertEqual(result["answers"][0]["source_answer_id"], "experience")

    def test_employer_preference_only_saved_options_and_explicit_alias(self):
        entry = self.entry("schedule", value=["Weekdays", "Weekends"], type="single_select", units=None,
                           kind="routine_preference", skill="", options=["Weekdays", "Weekends", "Never"],
                           aliases=["Preferred schedule?"], basis="employer_preference", authorized=True)
        self.bank["answers"] = [entry]
        self.save_bank()
        question = {"question": "Preferred schedule?", "type": "single_select", "kind": "routine_preference",
                    "options": entry["options"], "scope": {"country": "SG"},
                    "employer_preferred_option": "Weekends"}
        result = a.prepare_answers(self.root, self.record, [question], self.now)
        self.assertEqual(result["answers"][0]["answer"], "Weekends")
        question["employer_preferred_option"] = "Never"
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])
        question["question"] = "Can you start immediately?"
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])

    def test_authorization_sponsorship_and_consent_remain_separate(self):
        base = dict(type="boolean", units=None, skill="", value=True)
        self.bank["answers"] = [self.entry("legal", aliases=["Legally authorized?"], kind="legal_authorization", **base),
                                self.entry("sponsor", aliases=["Sponsorship required?"], kind="sponsorship", **base),
                                self.entry("consent", aliases=["Accept privacy notice?"], kind="consent",
                                           scope={"application_id": "app-synthetic"}, **base)]
        self.save_bank()
        questions = [{"question": wording, "type": "boolean", "kind": kind} for wording, kind in
                     [("Legally authorized?", "legal_authorization"), ("Sponsorship required?", "sponsorship"),
                      ("Accept privacy notice?", "consent")]]
        result = a.prepare_answers(self.root, self.record, questions, self.now)
        self.assertTrue(result["ready"])
        self.assertEqual({r["source_answer_id"] for r in result["answers"]}, {"legal", "sponsor", "consent"})
        other = {**self.record, "application_id": "app-another"}
        result = a.prepare_answers(self.root, other, questions, self.now)
        self.assertEqual(len(result["blocked_on_answer_ids"]), 1)
        self.bank["answers"][0].update(basis="employer_preference", authorized=True)
        self.save_bank()
        self.assertFalse(a.prepare_answers(self.root, self.record, questions, self.now)["ready"])

    def test_select_uses_exact_observed_option_without_boolean_coercion(self):
        self.bank["answers"] = [self.entry("choice", type="single_select", options=["Yes", "No"],
                                                units=None, kind="fact", skill="", value=" yes ", aliases=["Known fact?"])]
        self.save_bank()
        question = {"question": "Known fact?", "type": "single_select", "options": ["Yes", "No"]}
        result = a.prepare_answers(self.root, self.record, [question], self.now)
        self.assertEqual(result["answers"][0]["answer"], "Yes")
        self.assertEqual(result["answers"][0]["source_value"], " yes ")
        self.bank["answers"][0]["value"] = True
        self.save_bank()
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])
        question["options"] = ["Yes", "No", "Maybe"]
        self.assertFalse(a.prepare_answers(self.root, self.record, [question], self.now)["ready"])

    def test_multiselect_and_json_boolean_number_types(self):
        self.bank["answers"] = [self.entry("choice", type="multi_select", options=["One", "Two"],
                                                units=None, kind="fact", skill="", value=["one", "Two"], aliases=["Select facts?"])]
        self.save_bank()
        question = {"question": "Select facts?", "type": "multi_select", "options": ["One", "Two"]}
        result = a.prepare_answers(self.root, self.record, [question], self.now)
        self.assertEqual(result["answers"][0]["answer"], ["One", "Two"])
        result = self.known(value=True)
        self.assertFalse(result["ready"])

    def test_snapshot_expiry_after_preparation_is_rejected_without_mutation(self):
        result = self.known()
        before = (self.root / result["answers_path"]).read_bytes()
        self.assertFalse(self.valid(result, self.now + timedelta(days=4))["valid"])
        self.assertEqual(before, (self.root / result["answers_path"]).read_bytes())

    def test_future_source_confirmation_effective_date_and_option_changes_block(self):
        for patch in [{"confirmed_at": (self.now + timedelta(hours=1)).isoformat()},
                      {"effective_from": (self.now + timedelta(hours=1)).isoformat()}]:
            with self.subTest(patch=patch):
                self.assertFalse(self.known(**patch)["ready"])
        entry = self.entry("choice", type="single_select", units=None, kind="fact", skill="",
                           options=["Yes", "No"], value="Yes", aliases=["Known choice?"])
        self.bank["answers"] = [entry]
        self.save_bank()
        question = {"question": "Known choice?", "type": "single_select", "options": ["Yes", "No"]}
        prepared = a.prepare_answers(self.root, self.record, [question], self.now)
        entry["options"].append("Maybe")
        self.save_bank()
        self.assertFalse(self.valid(prepared)["valid"])

    def test_snapshot_revalidates_all_source_fields_even_without_version_bump(self):
        for patch in [{"value": 2}, {"version": 2}, {"scope": {"country": "US"}}, {"units": "months"},
                      {"review_due_at": self.now.isoformat()}, {"aliases": []}, {"source": "New confirmation"}]:
            with self.subTest(patch=patch):
                result = self.known()
                self.bank["answers"][0].update(patch)
                self.save_bank()
                self.assertFalse(self.valid(result)["valid"])

    def test_snapshot_tampering_dropped_row_and_duplicate_row_fail(self):
        result = self.known()
        original = copy.deepcopy(result["answers"])
        for key, value in [("answer", 9), ("source_value", 9), ("source_scope", {}), ("basis", "authorized_default"),
                           ("options", [1, 2]), ("units", "months"), ("application_id", "app-other")]:
            with self.subTest(key=key):
                result["answers"] = copy.deepcopy(original)
                result["answers"][0][key] = value
                self.assertFalse(self.valid(result)["valid"])
        result["answers"] = []
        self.assertFalse(self.valid(result)["valid"])
        result["answers"] = original * 2
        self.assertFalse(self.valid(result)["valid"])

    def test_legacy_manual_compatibility_is_explicit_and_never_allows_null(self):
        legacy = [{"question": "Previously handled?", "answer": False}]
        self.assertFalse(a.validate_snapshot(self.root, self.record, legacy, self.now)["valid"])
        result = a.validate_snapshot(self.root, self.record, legacy, self.now, allow_legacy=True)
        self.assertTrue(result["valid"])
        self.assertTrue(result["legacy_manual"])
        legacy[0]["answer"] = None
        self.assertFalse(a.validate_snapshot(self.root, self.record, legacy, self.now, allow_legacy=True)["valid"])
        result = self.known()
        del result["answers"][0]["source_fingerprint"]
        self.assertFalse(self.valid(result, allow_legacy=True)["valid"])

    def test_hash_version_fallback_for_explicit_legacy_source(self):
        entry = self.entry()
        del entry["version"]
        self.bank["answers"] = [entry]
        self.save_bank()
        result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
        self.assertEqual(len(result["answers"][0]["source_version"]), 64)
        self.assertTrue(self.valid(result)["valid"])

    def test_context_stable_private_and_unrelated_logging_ignored(self):
        self.known()
        self.save_record()
        self.write("resumes/registry.json", {"releases": [{"id": "synthetic-v1", "scope": "general", "status": "active"}]})
        self.write("profile/preferences.json", {"schedule": "Synthetic preference"})
        one = a.context(self.root, self.now)
        self.write("applications/log.csv", "synthetic log change")
        self.record["events"] = [{"at": self.now.isoformat(), "note": "Unrelated log"}]
        self.save_record()
        two = a.context(self.root, self.now + timedelta(seconds=1))
        self.assertEqual(one, two)
        self.assertTrue((self.root / one["path"]).is_relative_to(self.root / "profile"))
        self.bank["answers"][0]["value"] = 2
        self.save_bank()
        self.assertNotEqual(one["version"], a.context(self.root, self.now)["version"])
        self.assertFalse((self.root / "dashboard").exists())

    def test_context_changes_with_blockers_preferences_resume_and_expiry(self):
        self.known()
        first = a.context(self.root, self.now)["version"]
        expired = a.context(self.root, self.now + timedelta(days=4))
        self.assertNotEqual(first, expired["version"])
        self.assertFalse(expired["answers"][0]["usable"])
        self.record.update(status="awaiting_answer", blocked_on_answer_ids=["q-new"])
        self.save_record()
        second = a.context(self.root, self.now)["version"]
        self.assertNotEqual(first, second)
        self.write("profile/preferences.json", {"location": "Synthetic City"})
        third = a.context(self.root, self.now)["version"]
        self.assertNotEqual(second, third)
        self.write("resumes/registry.json", {"releases": [{"id": "resume-two", "scope": "general"}]})
        self.assertNotEqual(third, a.context(self.root, self.now)["version"])

    def test_context_never_labels_invalid_or_unauthorized_sources_usable(self):
        for patch in [{"value": "many"}, {"valid_until": "invalid"}, {"source": None},
                      {"basis": "authorized_default", "authorized": False}]:
            with self.subTest(patch=patch):
                self.known(**patch)
                self.assertFalse(a.context(self.root, self.now)["answers"][0]["usable"])

    def test_import_preview_commit_and_retry_persist_truth(self):
        self.save_record()
        a.prepare_answers(self.root, self.record, [self.question()], self.now)
        change = self.change([self.entry()])
        before = self.bytes_under_root()
        preview = a.import_answers(self.root, change, self.now, preview=True)
        self.assertFalse(preview["persisted"])
        self.assertEqual(before, self.bytes_under_root())
        self.assertEqual(preview["affected_jobs"][0]["application_id"], self.record["application_id"])
        self.assertTrue(preview["affected_jobs"][0]["answers_resolved"])
        result = a.import_answers(self.root, change, self.now)
        self.assertTrue(result["persisted"])
        persisted = self.read("profile/answers.json")
        self.assertEqual(persisted["answers"][0]["source"], "Synthetic user confirmation")
        self.assertEqual(persisted["answers"][0]["confirmed_at"], self.entry()["confirmed_at"])
        self.assertEqual(persisted["answers"][0]["scope"], {"country": "SG"})
        saved_bytes = (self.root / "profile/answers.json").read_bytes()
        repeat = a.import_answers(self.root, change, self.now + timedelta(days=20))
        self.assertTrue(repeat["already_imported"])
        self.assertEqual(saved_bytes, (self.root / "profile/answers.json").read_bytes())
        self.assertEqual(result["bank_version"], a.context(self.root, self.now)["bank_version"])

    def test_import_entire_batch_invalid_means_no_partial_writes(self):
        invalid = self.entry("invalid", type="number", value="many")
        change = self.change([self.entry(), invalid])
        before = self.bytes_under_root()
        with self.assertRaises(ValueError):
            a.import_answers(self.root, change, self.now)
        self.assertEqual(before, self.bytes_under_root())

    def test_import_rejects_stale_base_and_conflicting_import_change_ids(self):
        change = self.change([self.entry()])
        a.import_answers(self.root, change, self.now)
        stale = copy.deepcopy(change)
        stale["import_id"] = "different-import"
        with self.assertRaisesRegex(ValueError, "Stale"):
            a.import_answers(self.root, stale, self.now)
        conflict = copy.deepcopy(change)
        conflict["changes"][0]["answer"]["value"] = 3
        with self.assertRaisesRegex(ValueError, "Import ID"):
            a.import_answers(self.root, conflict, self.now)
        conflict = self.change([self.entry(value=3)], identity="new-import")
        conflict["changes"][0]["change_id"] = change["changes"][0]["change_id"]
        with self.assertRaisesRegex(ValueError, "Change ID"):
            a.import_answers(self.root, conflict, self.now)

    def test_duplicate_change_in_another_import_is_harmless(self):
        first = self.change([self.entry()])
        a.import_answers(self.root, first, self.now)
        second = self.change([self.entry()], identity="second-import")
        second["changes"] = first["changes"]
        version = a.bank_version(a._load_bank(self.root))
        result = a.import_answers(self.root, second, self.now)
        self.assertEqual(result["applied_change_ids"], [])
        self.assertEqual(version, result["bank_version"])
        self.assertIn("second-import", self.read("profile/answers.json")["imports"])

    def test_import_schema_scope_confirmation_expiry_options_and_defaults(self):
        cases = [
            {"scope": "Some employer"}, {"scope": {"city": "Synthetic City"}},
            {"source": ""}, {"confirmed_at": "2026-10-19T12:00:00"},
            {"confirmed_at": (self.now + timedelta(days=1)).isoformat()},
            {"valid_until": self.now.isoformat()}, {"review_due_at": self.now.isoformat()},
            {"kind": "consent", "scope": {}}, {"unknown_key": "value"},
            {"basis": "authorized_default", "authorized": False},
            {"basis": "authorized_default", "authorized": True, "skill": "Undocumented"},
            {"type": "single_select", "value": "Maybe", "options": ["Yes", "No"]},
            {"type": "single_select", "value": "Yes", "options": ["Yes", " yes "]},
            {"aliases": [{"question": "Python experience?", "units": "months"}]},
            {"scope": {"company": "Example Company"}, "aliases": [{"question": "Python experience?", "scope": {}}]},
        ]
        for patch in cases:
            with self.subTest(patch=patch):
                before = self.bytes_under_root()
                with self.assertRaises(ValueError):
                    a.import_answers(self.root, self.change([self.entry(**patch)]), self.now)
                self.assertEqual(before, self.bytes_under_root())

    def test_import_malformed_and_duplicate_batches_make_no_writes(self):
        malformed = []
        first = self.change([self.entry()])
        malformed.append({**first, "unknown": True})
        malformed.append({**first, "changes": []})
        malformed.append({**first, "changes": [{"change_id": "new", "answer": None}]})
        malformed.append({**first, "changes": [{"change_id": "new", "answer": {"value": 1}}]})
        malformed.append({**first, "changes": first["changes"] * 2})
        second = copy.deepcopy(first["changes"][0])
        second["change_id"] = "other-id"
        malformed.append({**first, "changes": first["changes"] + [second]})
        for change in malformed:
            with self.subTest(change=change):
                before = self.bytes_under_root()
                with self.assertRaises(ValueError):
                    a.import_answers(self.root, change, self.now)
                self.assertEqual(before, self.bytes_under_root())

    def test_import_dedup_receipt_reports_current_persisted_version(self):
        first = self.change([self.entry()])
        imported = a.import_answers(self.root, first, self.now)
        second = self.change([self.entry(value=2, version=2)], identity="second-import")
        latest = a.import_answers(self.root, second, self.now)
        repeat = a.import_answers(self.root, first, self.now)
        self.assertEqual(repeat["bank_version"], latest["bank_version"])
        self.assertEqual(repeat["imported_bank_version"], imported["bank_version"])

    def test_authorized_default_and_preference_import_roundtrip(self):
        default = self.entry("default", basis="authorized_default", authorized=True)
        preference = self.entry("preference", type="single_select", units=None, kind="routine_preference", skill="",
                                basis="employer_preference", authorized=True, value=["Yes"], options=["Yes", "No"],
                                aliases=["Routine schedule?"])
        result = a.import_answers(self.root, self.change([default, preference]), self.now)
        self.assertTrue(result["persisted"])
        result = a.prepare_answers(self.root, self.record, [self.question(),
            {"question": "Routine schedule?", "kind": "routine_preference", "type": "single_select",
             "options": ["Yes", "No"], "employer_preferred_option": "Yes"}], self.now)
        self.assertTrue(result["ready"])
        self.assertEqual({r["basis"] for r in result["answers"]}, {"authorized_default", "employer_preference"})
        self.assertTrue(all(e["usable"] for e in a.context(self.root, self.now)["answers"]))

    def test_atomic_import_failure_preserves_authoritative_bytes(self):
        before = self.bytes_under_root()
        with mock.patch.object(a.os, "replace", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaises(OSError):
                a.import_answers(self.root, self.change([self.entry()]), self.now)
        self.assertEqual(before, self.bytes_under_root())

    def test_import_can_persist_null_but_it_does_not_resolve_a_question(self):
        result = a.import_answers(self.root, self.change([self.entry(value=None)]), self.now)
        self.assertTrue(result["persisted"])
        result = a.prepare_answers(self.root, self.record, [self.question()], self.now)
        self.assertFalse(result["ready"])

    def test_import_update_requires_new_source_version_and_invalidates_snapshot(self):
        prepared = self.known()
        with self.assertRaisesRegex(ValueError, "new version"):
            a.import_answers(self.root, self.change([self.entry(value=2)]), self.now)
        a.import_answers(self.root, self.change([self.entry(version=2, value=2)]), self.now)
        self.assertFalse(self.valid(prepared)["valid"])

    def blocked_job(self, app, extra=False, status="awaiting_answer"):
        record = {**self.record, "application_id": app, "status": status}
        questions = [self.question()]
        if extra:
            questions.append({"question": "Other unknown fact?"})
        result = a.prepare_answers(self.root, record, questions, self.now)
        record["blocked_on_answer_ids"] = result["blocked_on_answer_ids"]
        self.save_record(record)
        return record

    def test_requeue_shared_blocker_all_only_and_terminal_untouched(self):
        self.blocked_job("app-one")
        self.blocked_job("app-two")
        self.blocked_job("app-extra", extra=True)
        self.blocked_job("app-submitted", status="submitted")
        self.blocked_job("app-uncertain", status="submission_uncertain")
        a.import_answers(self.root, self.change([self.entry()]), self.now)
        before = self.bytes_under_root()
        calls = []
        def check(root, record, now):
            calls.append(record["application_id"])
            return True
        result = a.requeue(self.root, self.now, resume_check=check, eligibility_check=check)
        self.assertEqual(result["runnable_ids"], ["app-one", "app-two"])
        self.assertEqual(result["waiting"][0]["application_id"], "app-extra")
        self.assertEqual(result["untouched_ids"], ["app-submitted", "app-uncertain"])
        self.assertEqual(sorted(calls), ["app-one", "app-one", "app-two", "app-two"])
        self.assertEqual(before, self.bytes_under_root())

    def test_requeue_requires_fresh_resume_and_current_eligibility_callbacks(self):
        self.blocked_job("app-one")
        a.import_answers(self.root, self.change([self.entry()]), self.now)
        result = a.requeue(self.root, self.now)
        self.assertEqual(result["runnable_ids"], [])
        self.assertEqual(result["waiting"][0]["reasons"], ["resume_check_required", "eligibility_check_required"])
        for resume, eligibility in [(False, True), (True, False), ("true", True)]:
            result = a.requeue(self.root, self.now, resume_check=lambda *args: resume,
                               eligibility_check=lambda *args: eligibility)
            self.assertEqual(result["runnable_ids"], [])
        def expired(*args):
            raise ValueError("Synthetic resume is stale")
        result = a.requeue(self.root, self.now, resume_check=expired, eligibility_check=lambda *args: True)
        self.assertEqual(result["waiting"][0]["reasons"], ["resume_check_failed"])

    def test_requeue_retains_unknown_blocker_and_confirmed_history(self):
        record = self.blocked_job("app-one")
        record["blocked_on_answer_ids"].append("q-untracked")
        self.save_record(record)
        other = self.blocked_job("app-history")
        other["submissions"] = [{"id": "synthetic-confirmation"}]
        self.save_record(other)
        a.import_answers(self.root, self.change([self.entry()]), self.now)
        result = a.requeue(self.root, self.now, resume_check=lambda *args: True, eligibility_check=lambda *args: True)
        self.assertEqual(result["runnable_ids"], [])
        self.assertEqual(result["waiting"][0]["blocked_on_answer_ids"], ["q-untracked"])
        self.assertEqual(result["untouched_ids"], ["app-history"])

    def test_private_artifacts_are_mode_0600_and_immutable(self):
        result = self.known()
        for name in (result["answers_path"], result["inbox_path"]):
            self.assertEqual(os.stat(self.root / name).st_mode & 0o777, 0o600)
        (self.root / result["answers_path"]).write_text("tampered")
        with self.assertRaisesRegex(ValueError, "immutable"):
            a.prepare_answers(self.root, self.record, [self.question()], self.now)

    def test_path_escape_and_symlinks_fail_without_external_writes(self):
        with self.assertRaises(ValueError):
            a.prepare_answers(self.root, {**self.record, "application_id": "../outside"}, [], self.now)
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "applications").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            a.prepare_answers(self.root, self.record, [], self.now)
        self.assertEqual(list(outside.iterdir()), [])

    def test_two_workspaces_and_relocation_do_not_cross_read(self):
        self.known()
        first = a.context(self.root, self.now)
        with tempfile.TemporaryDirectory() as empty:
            second = a.context(Path(empty), self.now)
            self.assertEqual(second["answers"], [])
            self.assertNotEqual(first["version"], second["version"])
        relocated = self.root / "relocated"
        relocated.mkdir()
        import shutil
        for name in ("profile", "applications"):
            shutil.copytree(self.root / name, relocated / name)
        again = a.context(relocated, self.now)
        self.assertEqual(first, again)

    def test_naive_time_unsupported_schema_and_nonfinite_values_are_rejected(self):
        with self.assertRaises(ValueError):
            a.prepare_answers(self.root, self.record, [], self.now.replace(tzinfo=None))
        for patch in [{"type": "upload"}, {"options": [float("nan")]}, {"scope": {"unknown": "x"}}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                a.prepare_answers(self.root, self.record, [self.question(**patch)], self.now)
        change = self.change([self.entry()])
        change["schema_version"] = True
        with self.assertRaises(ValueError):
            a.import_answers(self.root, change, self.now)


if __name__ == "__main__":
    unittest.main()
