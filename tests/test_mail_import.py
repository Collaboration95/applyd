"""Only fictional messages and temporary workspaces; never reads an inbox."""

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mail_import
from mail_import import read_export, sync_local
from workspace import writer


NOW = "2026-10-22T12:00:00+08:00"


def message(message_id="mail-1", subject="Interview invitation", **extra):
    return {"message_id": message_id, "thread_id": "synthetic-thread", "subject": subject,
            "body": "Synthetic private mail body", "received_at": "2026-10-20T09:00:00+08:00",
            "platform_job_id": "listing-7", **extra}


class MailImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mail-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def application(self, app_id="app-example", **extra):
        relative = "applications/records/" + app_id + "/confirmation.txt"
        confirmation = self.root / relative
        confirmation.parent.mkdir(parents=True, exist_ok=True)
        confirmation.write_bytes(b"Fictional observed application confirmation")
        record = {"application_id": app_id, "company": "Example Labs", "role": "Backend Engineer",
                  "platform_job_id": "listing-7", "status": "submitted",
                  "submitted_at": "2026-10-18T10:00:00+08:00", "events": [],
                  "submissions": [{"submitted_at": "2026-10-18T10:00:00+08:00",
                                   "confirmation_path": relative,
                                   "confirmation_sha256": hashlib.sha256(confirmation.read_bytes()).hexdigest()}],
                  **extra}
        self.write("applications/records/" + app_id + "/record.json", record)
        return record

    def record(self, app_id="app-example"):
        return json.loads((self.root / "applications/records" / app_id / "record.json").read_text())

    def state(self):
        return json.loads((self.root / mail_import.STATE_PATH).read_text())

    def sync(self, messages, now=NOW):
        with writer(self.root):
            return sync_local(self.root, messages, now)

    def evidence(self, event, expected):
        evidence = event["evidence"]
        self.assertEqual(evidence["message_id"], expected["message_id"])
        path = self.root / evidence["path"]
        self.assertTrue(path.relative_to(self.root).is_relative_to(mail_import.MESSAGES_PATH))
        self.assertEqual(json.loads(path.read_text()), expected)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), evidence["sha256"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_blocker_and_repeated_import_are_content_free(self):
        import workspace
        command=[sys.executable, str(Path(workspace.__file__)), '--workspace', str(self.root), '--defer-export', 'mail-sync']
        missing=subprocess.run(command,text=True,capture_output=True)
        self.assertEqual(missing.returncode,2)
        self.assertEqual(json.loads(missing.stdout)['blocker']['type'],'credential_required')
        self.application()
        export=self.write('synthetic-mail-export.json',[message()])
        for expected in (1,0):
            process=subprocess.run(command+['--file',str(export)],text=True,capture_output=True)
            self.assertEqual(process.returncode,0,process.stderr)
            result=json.loads(process.stdout)
            self.assertEqual(result['counts']['outcomes'],expected)
            self.assertEqual(result['export']['status'],'deferred')
            self.assertNotIn('Synthetic private mail body',process.stdout)
        self.assertTrue(json.loads((self.root/'applications/export-state.json').read_text())['dirty'])

    def test_all_supported_outcomes_persist_with_exact_private_evidence(self):
        original = self.application()
        cases = [("Application received", "acknowledged"), ("Coding test", "assessment"),
                 ("Interview invitation", "interview"), ("Application was unsuccessful", "rejected")]
        for index, (subject, status) in enumerate(cases):
            msg = message("mail-" + str(index), subject, received_at=f"2026-10-{19 + index}T09:00:00+08:00")
            result = self.sync([msg])
            self.assertEqual(result["counts"]["outcomes"], 1)
            event = self.record()["outcome_events"][-1]
            self.assertEqual(event["status"], status)
            self.evidence(event, msg)
        saved = self.record()
        self.assertEqual(saved["submissions"], original["submissions"])
        self.assertEqual(saved["submitted_at"], original["submitted_at"])
        self.assertEqual(saved["status"], "rejected")
        self.assertNotIn("Synthetic private mail body", json.dumps(saved))

    def test_duplicates_in_batch_and_across_restart_never_append_events(self):
        self.application()
        msg = message("exact/message-id@example.invalid")
        self.assertEqual(self.sync([msg, deepcopy(msg)])["counts"]["outcomes"], 1)
        before = self.record()
        importlib.reload(mail_import)
        result = self.sync([msg], "2026-10-23T12:00:00+08:00")
        self.assertEqual(result["counts"]["duplicates"], 1)
        self.assertEqual(result["counts"]["outcomes"], 0)
        self.assertEqual(self.record(), before)
        self.assertEqual(self.state()["processed_message_ledger"], [msg["message_id"]])
        self.evidence(before["outcome_events"][0], msg)

    def test_crash_after_record_before_ledger_recovers_without_resupplied_mail(self):
        self.application()
        original_write = mail_import._atomic_json

        def interrupted(path, value):
            if path == self.root.resolve() / mail_import.STATE_PATH:
                self.assertEqual(len(self.record()["outcome_events"]), 1)
                raise KeyboardInterrupt("simulated process interruption")
            original_write(path, value)

        with patch.object(mail_import, "_atomic_json", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.sync([message()])
        before = self.record()
        # Changed follow-up policy would defeat event equality deduplication;
        # message identity must still win before matching/apply_outcome.
        self.write("settings.json", {"follow_up_days": 3})
        importlib.reload(mail_import)
        result = self.sync([], "2026-10-23T12:00:00+08:00")
        self.assertEqual(result["counts"]["recovered"], 1)
        self.assertEqual(result["counts"]["outcomes"], 0)
        self.assertEqual(self.record(), before)
        self.assertEqual(self.state()["processed_message_ledger"], ["mail-1"])

    def test_crash_before_event_write_replays_durable_inbox(self):
        self.application()
        original_write = mail_import._atomic_json

        def interrupted(path, value):
            if path.name == "record.json":
                raise KeyboardInterrupt
            original_write(path, value)

        with patch.object(mail_import, "_atomic_json", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.sync([message()])
        self.assertNotIn("outcome_events", self.record())
        self.assertEqual(self.sync([])["counts"]["outcomes"], 1)
        self.assertEqual(len(self.record()["outcome_events"]), 1)

    def test_partial_batch_crash_replays_remaining_message_without_duplicate(self):
        self.application()
        original_write = mail_import._atomic_json
        writes = 0

        def interrupted(path, value):
            nonlocal writes
            if path.name == "record.json":
                writes += 1
                if writes == 2:
                    raise KeyboardInterrupt
            original_write(path, value)

        with patch.object(mail_import, "_atomic_json", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.sync([message("first"), message("second", "Coding test")])
        self.assertEqual(len(self.record()["outcome_events"]), 1)
        self.assertEqual(self.sync([])["counts"]["outcomes"], 1)
        self.assertEqual(len(self.record()["outcome_events"]), 2)
        self.assertEqual(set(self.state()["processed_message_ledger"]), {"first", "second"})

    def test_io_failure_is_redacted_and_does_not_advance_ledger(self):
        self.application()
        write = mail_import._atomic_json

        def failed(path, value):
            if path == self.root.resolve() / mail_import.STATE_PATH:
                raise OSError("private-sender@example.invalid /private/synthetic-path")
            write(path, value)

        with patch.object(mail_import, "_atomic_json", side_effect=failed):
            with self.assertRaisesRegex(ValueError, "^mail_import_failed$"):
                self.sync([message()])
        self.assertFalse((self.root / mail_import.STATE_PATH).exists())
        self.assertEqual(self.sync([])["counts"]["recovered"], 1)
        self.assertEqual(len(self.record()["outcome_events"]), 1)

    def test_ambiguous_match_retains_full_message_after_later_cursor_and_retry(self):
        self.application()
        self.application("app-other")
        pending = message("pending")
        result = self.sync([pending, message("latest", "Newsletter", received_at="2026-10-21T11:00:00+08:00")])
        self.assertEqual(result["counts"]["queued"], 1)
        self.assertEqual(self.state()["attention_queue"][0]["message"], pending)
        self.assertEqual(self.state()["cursor"], "2026-10-21T11:00:00+08:00")
        self.assertNotIn("pending", self.state()["processed_message_ledger"])
        self.application("app-other", platform_job_id="different")
        result = self.sync([])
        self.assertEqual(result["counts"]["outcomes"], 1)
        self.assertEqual(self.state()["attention_queue"], [])
        self.assertEqual(self.record()["outcome_events"][0]["evidence"]["message_id"], "pending")

    def test_conflicting_cues_stay_queued_and_are_not_lost_or_ledgered(self):
        self.application()
        msg = message("conflicting", body="Your application was unsuccessful")
        for inputs in ([msg], []):
            result = self.sync(inputs)
            self.assertEqual(result["counts"]["queued"], 1)
            self.assertEqual(result["counts"]["outcomes"], 0)
            self.assertEqual(self.state()["attention_queue"][0]["reason"], "conflicting_outcome_cues")
            self.assertEqual(self.state()["processed_message_ledger"], [])
        self.assertNotIn("outcome_events", self.record())

    def test_job_id_cannot_override_identity_or_message_before_submission(self):
        self.application()
        for msg in [message("company", company="Other Employer"), message("role", role="Sales Engineer"),
                    message("early", received_at="2026-10-18T01:59:59Z")]:
            self.sync([msg])
        reasons = {item["reason"] for item in self.state()["attention_queue"]}
        self.assertEqual(reasons, {"contradictory_application_identity", "message_predates_confirmation"})
        self.assertNotIn("outcome_events", self.record())

    def test_missing_tampered_or_status_only_confirmation_never_matches(self):
        for mutation in ("missing", "tampered", "status_only"):
            with self.subTest(mutation=mutation):
                original = self.application()
                confirmation = self.root / original["submissions"][0]["confirmation_path"]
                if mutation == "missing":
                    confirmation.unlink()
                elif mutation == "tampered":
                    confirmation.write_text("different bytes")
                else:
                    self.application(submissions=[])
                self.sync([message("unconfirmed-" + mutation)])
                self.assertNotIn("outcome_events", self.record())
        self.assertTrue(all(q["reason"] == "application_not_confirmed" for q in self.state()["attention_queue"]))

    def test_recruiter_contact_is_private_read_only_and_has_no_submission_or_outcome(self):
        original = self.application()
        msg = message("recruiter", "Recruiter note", body="I am a recruiter")
        result = self.sync([msg])
        self.assertEqual(result["counts"]["contacts"], 1)
        self.assertEqual(result["counts"]["outcomes"], 0)
        self.assertEqual(self.record(), original)
        contacts = json.loads((self.root / mail_import.CONTACTS_PATH).read_text())
        self.assertTrue(contacts[0]["read_only"])
        self.evidence(contacts[0], msg)
        self.sync([msg])
        self.assertEqual(json.loads((self.root / mail_import.CONTACTS_PATH).read_text()), contacts)

    def test_contact_crash_before_ledger_recovers_by_message_id(self):
        self.application()
        write = mail_import._atomic_json

        def interrupted(path, value):
            if path == self.root.resolve() / mail_import.STATE_PATH:
                raise KeyboardInterrupt
            write(path, value)

        with patch.object(mail_import, "_atomic_json", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.sync([message("recruiter", "Recruiter note", body="I am a recruiter")])
        result = self.sync([])
        self.assertEqual(result["counts"]["recovered"], 1)
        self.assertEqual(result["counts"]["contacts"], 0)
        self.assertEqual(len(json.loads((self.root / mail_import.CONTACTS_PATH).read_text())), 1)

    def test_unknown_recruiter_and_recommendation_never_fabricate_application(self):
        self.sync([message("contact", "Recruiter note", body="I am a recruiter"), message("alert", "Recommended jobs")])
        self.assertEqual(self.state()["processed_message_ledger"], ["alert"])
        self.assertEqual(self.state()["attention_queue"][0]["reason"], "no_application_match")
        self.assertFalse((self.root / "applications/records").exists())

    def test_duplicate_id_with_changed_payload_refuses_without_overwriting_evidence(self):
        self.application()
        self.sync([message()])
        before = self.record()
        with self.assertRaisesRegex(ValueError, "mail_message_id_collision"):
            self.sync([message(body="Changed synthetic text")])
        self.assertEqual(self.record(), before)
        self.evidence(before["outcome_events"][0], message())

    def test_safe_summary_has_counts_and_no_message_or_identity_fields(self):
        self.application()
        result = self.sync([message("private-id@example.invalid", company="Example Labs", role="Backend Engineer")])
        self.assertEqual(set(result), {"status", "counts"})
        serialized = json.dumps(result)
        for private in ("private-id", "Example Labs", "Backend Engineer", "Interview", "Synthetic private mail body"):
            self.assertNotIn(private, serialized)
        self.assertFalse((self.root / "dashboard").exists())

    def test_missing_adapter_is_typed_blocker_without_workspace_access(self):
        result = sync_local(self.root / "does-not-exist", None, None)
        self.assertEqual(result, {"status": "blocked", "blocker": {
            "type": "credential_required", "reason": "live_verification_requires_credentials"}})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_messages_fail_before_private_writes_with_fixed_error(self):
        for msg in (message(message_id=""), message(received_at="not-a-time"), message(subject=42)):
            with self.assertRaises(ValueError) as error:
                sync_local(self.root, [msg], NOW)
            self.assertNotIn("Synthetic private", str(error.exception))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_recording_time_is_singapore_with_explicit_offset(self):
        self.application()
        self.sync([message()], datetime.fromisoformat("2026-10-22T04:00:00Z"))
        self.assertEqual(self.record()["outcome_events"][0]["recorded_at"], NOW)

    def test_private_directory_symlink_is_refused(self):
        self.application()
        destination = self.root / "public-output"
        destination.mkdir()
        (self.root / "applications/mail").symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "mail_artifact_symlink_refused"):
            self.sync([message()])
        self.assertEqual(list(destination.iterdir()), [])

    def test_local_export_shapes_and_redacted_parse_errors(self):
        for data in ([message()], {"messages": [message()]}):
            path = self.write("synthetic-export.json", data)
            self.assertEqual(read_export(path), [message()])
        path.write_text("private synthetic malformed content")
        with self.assertRaisesRegex(ValueError, "^mail_json_read_failed$"):
            read_export(path)


if __name__ == "__main__":
    unittest.main()
