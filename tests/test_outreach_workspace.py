"""Temporary synthetic workspace integration only; no private files or accounts."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import outreach
import outreach_workspace as ow
import settings
import workspace as w
from test_outreach import application, NOW, POLICY


class WorkspaceOutreachTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime.fromisoformat(NOW)
        self.project = {"id": "example-api", "active": True,
                        "bullets": [{"id": "api-1", "text": "Built a Python API."}]}
        w.atomic_json(self.root / "projects/catalog.json", {"projects": [self.project]})
        self.configure(outreach=POLICY, timezone="Asia/Singapore")
        self.run = settings.run_start(self.root, "outreach-run", self.now)
        record = application(run_id="historical-run")
        record["prepared"] = {"id": "past-prepared", "run_id": "historical-run"}
        saved = record["submissions"][0]
        saved["run_id"] = "historical-run"
        saved["selected_projects"][0]["content_hash"] = w.project_fingerprint(self.project)
        for path_field, hash_field, text in (("exact_resume_copy", "sha256", "Synthetic submitted PDF"),
                ("confirmation_path", "confirmation_sha256", "Synthetic confirmed application")):
            path = self.root / saved[path_field]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            saved[hash_field] = w.digest(path)
        self.save(record)
        self.original = deepcopy(record)

    def configure(self, **changes):
        current = settings.load_settings(self.root)
        current.update(changes)
        w.atomic_json(self.root / "settings.json", current)

    def record(self):
        return w.read(self.root / "applications/records/app-a/record.json")

    def save(self, record):
        w.persist_record(self.root, record)

    def create(self, **changes):
        args = dict(root=self.root, run_id="outreach-run", application_id="app-a",
                    contact_id="contact-fictional", channel="linkedin",
                    project_id="example-api", bullet_id="api-1", now=self.now)
        args.update(changes)
        with w.writer(self.root):
            return ow.create_follow_up(**args)

    def adapter(self, *, result="actual-synthetic-provider-id", error=None):
        owner = self
        class Adapter:
            calls = 0
            def create_draft(self, **args):
                self.calls += 1
                owner.assertEqual(owner.record()["outreach"][0]["status"], "draft_uncertain")
                owner.assertEqual(owner.record()["outreach"][0]["follow_up_id"], args["reservation_id"])
                if error:
                    raise error
                return result
        return Adapter()

    def test_local_note_preserves_submission_run_and_exports_structured_sources(self):
        result = self.create()
        self.assertEqual(result["status"], "note_created")
        record = self.record()
        for field in ("run_id", "prepared", "submissions", "events", "submitted_at", "status"):
            self.assertEqual(record[field], self.original[field], field)
        event = record["outreach"][0]
        self.assertEqual(event["run_id"], "outreach-run")
        self.assertEqual(event["run_settings_hash"], self.run["settings_hash"])
        self.assertEqual(event["sources"]["project"]["text"], self.project["bullets"][0]["text"])
        self.assertTrue(result["export"]["ok"])
        data = w.read(self.root / "dashboard/data/applications.json")
        self.assertEqual(data["applications"][0]["outreach"], record["outreach"])
        self.assertIn('"outreach"', (self.root / "dashboard/data.js").read_text())
        self.assertEqual(self.create()["reason"], "already_followed_up")
        self.assertEqual(len(self.record()["outreach"]), 1)

    def test_requires_existing_dedicated_run_never_attaches_a_historical_run(self):
        for identity in (None, "missing-run"):
            with self.subTest(identity=identity), self.assertRaises(settings.SettingsError):
                self.create(run_id=identity)
        settings.run_start(self.root, "historical-run", self.now)
        with self.assertRaisesRegex(settings.SettingsError, "dedicated"):
            self.create(run_id="historical-run")
        self.assertNotIn("outreach", self.record())
        self.assertEqual(self.record()["run_id"], "historical-run")

    def test_disabled_current_or_frozen_and_legacy_snapshot(self):
        self.configure(outreach={**POLICY, "enabled": False})
        self.assertEqual(self.create()["reason"], "disabled")
        settings.run_start(self.root, "disabled-run", self.now)
        self.configure(outreach=POLICY)
        self.assertEqual(self.create(run_id="disabled-run")["reason"], "disabled")
        legacy = settings.run_start(self.root, "legacy-run", self.now)
        legacy["settings"].pop("outreach")
        legacy["settings_hash"] = hashlib.sha256(json.dumps(legacy["settings"], sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        path = self.root / "applications/runs/legacy-run/run.json"
        w.atomic_json(path, legacy)
        original = path.read_bytes()
        self.assertEqual(self.create(run_id="legacy-run")["reason"], "disabled")
        self.assertEqual(path.read_bytes(), original)

    def test_policy_intersection_is_strict_and_does_not_mutate(self):
        a = {"outreach": {**POLICY, "business_days": 2, "daily_draft_cap": 8, "do_not_contact": ["A"]}}
        b = {"outreach": {**POLICY, "business_days": 12, "daily_draft_cap": 1, "do_not_contact": ["B"]}}
        before = deepcopy((a, b))
        self.assertEqual(ow.effective_policy(a, b), {**POLICY, "business_days": 12,
            "daily_draft_cap": 1, "do_not_contact": ["A", "B"]})
        self.assertEqual((a, b), before)

    def test_larger_wait_smaller_cap_and_dnc_union(self):
        self.configure(outreach={**POLICY, "business_days": 12})
        self.assertEqual(self.create()["reason"], "not_due")
        self.configure(outreach={**POLICY, "daily_draft_cap": 0})
        self.assertEqual(self.create()["reason"], "daily_cap")
        self.configure(outreach={**POLICY, "do_not_contact": ["Example Company"]})
        settings.run_start(self.root, "dnc-run", self.now)
        self.configure(outreach=POLICY)
        self.assertEqual(self.create(run_id="dnc-run")["reason"], "do_not_contact")
        self.assertNotIn("outreach", self.record())

    def test_both_modes_and_deadlines_block(self):
        for mode in ("dry_run", "save_only"):
            self.configure(mode=mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(settings.SettingsError, "mode:"):
                self.create()
            settings.run_start(self.root, mode, self.now)
            self.configure(mode="apply")
            with self.assertRaisesRegex(settings.SettingsError, "mode:"):
                self.create(run_id=mode)
        self.configure(per_run_minutes=1)
        with self.assertRaisesRegex(settings.SettingsError, "deadline"):
            self.create(now=self.now + timedelta(minutes=1))
        settings.run_start(self.root, "short-run", self.now)
        self.configure(per_run_minutes=None)
        with self.assertRaisesRegex(settings.SettingsError, "deadline"):
            self.create(run_id="short-run", now=self.now + timedelta(minutes=1))
        with self.assertRaisesRegex(settings.SettingsError, "not_started"):
            self.create(now=self.now - timedelta(seconds=1))

    def test_stop_and_live_guard_precede_adapter(self):
        adapter = self.adapter()
        (self.root / "applications/STOP").write_text("Synthetic stop")
        with patch.object(w.safety, "local_notification"):
            with self.assertRaises(w.safety.SafetyBlocked):
                self.create(channel="email", adapter=adapter)
        self.assertEqual(adapter.calls, 0)
        self.assertNotIn("outreach", self.record())

    def test_current_catalog_changed_inactive_absent_or_wrong_bullet_blocks(self):
        for project in ({**self.project, "active": False}, {**self.project, "title": "Changed"},
                {**self.project, "bullets": []}):
            w.atomic_json(self.root / "projects/catalog.json", {"projects": [project]})
            self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        w.atomic_json(self.root / "projects/catalog.json", {"projects": []})
        self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        self.assertNotIn("outreach", self.record())

    def test_original_pdf_missing_selected_provenance_blocks(self):
        record = self.record()
        record["submissions"][0].update(selected_projects=[], original_source={"id": "original"})
        self.save(record)
        self.assertEqual(self.create()["reason"], "unverifiable_project_source")

    def test_exact_submitted_resume_and_confirmation_bytes_are_required(self):
        for field in ("exact_resume_copy", "confirmation_path"):
            path = self.root / self.record()["submissions"][0][field]
            original = path.read_bytes()
            for content in (b"Changed synthetic file", b"", b" \n"):
                path.write_bytes(content)
                self.assertEqual(self.create()["reason"], "unverifiable_project_source")
            path.unlink()
            self.assertEqual(self.create()["reason"], "unverifiable_project_source")
            path.write_bytes(original)

    def test_paths_cannot_escape_or_follow_external_symlinks(self):
        saved = self.record()["submissions"][0]
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "synthetic.pdf"
            outside.write_text("Synthetic submitted PDF")
            link = self.root / "escape.pdf"
            link.symlink_to(outside)
            for value in (str(outside), "../escape.pdf", "escape.pdf"):
                record = deepcopy(self.original)
                record["submissions"][0]["exact_resume_copy"] = value
                self.save(record)
                self.assertEqual(self.create()["reason"], "unverifiable_project_source")
            with self.assertRaises(outreach.OutreachError):
                ow.safe_evidence(self.root, outside)
        self.save(self.original)
        self.assertEqual(ow.safe_evidence(self.root, saved["confirmation_path"])["sha256"], saved["confirmation_sha256"])

    def test_missing_credentials_blocker_exports_without_fake_created_id(self):
        result = self.create(channel="email")
        self.assertEqual(result["reason"], "credentials_required")
        self.assertNotIn("follow_up_id", result)
        self.assertNotIn("outreach", self.record())
        payload = w.read(self.root / "dashboard/data/applications.json")
        self.assertEqual(payload["applications"][0]["outreach_blocker"]["reason"], "credentials_required")

    def test_adapter_uncertainty_persists_exports_and_blocks_restart(self):
        adapter = self.adapter(error=TimeoutError("synthetic secret never log"))
        result = self.create(channel="email", adapter=adapter)
        self.assertEqual(result["status"], "draft_uncertain")
        self.assertEqual(adapter.calls, 1)
        self.assertTrue(result["export"]["ok"])
        self.assertEqual(self.create(channel="email", adapter=adapter)["reason"], "draft_uncertain")
        self.assertEqual(adapter.calls, 1)
        self.assertNotIn("synthetic secret", json.dumps(self.record()))

    def test_successful_email_uses_actual_adapter_id(self):
        adapter = self.adapter()
        self.assertEqual(self.create(channel="email", adapter=adapter)["status"], "draft_created")
        self.assertEqual(self.record()["outreach"][0]["adapter_id"], "actual-synthetic-provider-id")

    def test_failed_completion_write_exports_durable_uncertainty(self):
        original = w.persist_record
        calls = 0
        def failing(root, record):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Synthetic completion failure")
            return original(root, record)
        adapter = self.adapter()
        with patch.object(w, "persist_record", side_effect=failing):
            with self.assertRaises(OSError):
                self.create(channel="email", adapter=adapter)
        self.assertEqual(adapter.calls, 1)
        payload = w.read(self.root / "dashboard/data/applications.json")
        self.assertEqual(payload["applications"][0]["outreach"][0]["status"], "draft_uncertain")
        self.assertEqual(self.create()["reason"], "draft_uncertain")

    def test_reconcile_requires_saved_nonempty_evidence_and_note(self):
        result = self.create(channel="email", adapter=self.adapter(error=TimeoutError()))
        evidence = self.root / "reconciliation.txt"
        for content in (None, "", " \n"):
            if content is not None:
                evidence.write_text(content)
            with self.assertRaises(outreach.OutreachError):
                ow.reconcile_follow_up(self.root, "app-a", result["follow_up_id"], self.now,
                    resolution="not_created", evidence=evidence, note="User checked")
        evidence.write_text("Synthetic user confirmed absent provider draft")
        with self.assertRaises(outreach.OutreachError):
            ow.reconcile_follow_up(self.root, "app-a", result["follow_up_id"], self.now,
                resolution="not_created", evidence=evidence, note=" ")
        with w.writer(self.root):
            resolved = ow.reconcile_follow_up(self.root, "app-a", result["follow_up_id"], self.now,
                resolution="not_created", evidence=evidence, note="User checked")
        self.assertEqual(resolved["status"], "not_created")
        self.assertEqual(self.record()["outreach"][0]["reconciliation"]["evidence_sha256"], w.digest(evidence))
        self.assertNotEqual(self.create()["follow_up_id"], result["follow_up_id"])

    def test_manual_send_records_actual_time_without_regrouping_submission(self):
        result = self.create()
        evidence = self.root / "manual-send.txt"
        evidence.write_text("Synthetic explicit user confirmation")
        occurred = self.now + timedelta(minutes=2)
        recorded = self.now + timedelta(hours=2)
        with w.writer(self.root):
            sent = ow.confirm_manual_send(self.root, "app-a", result["follow_up_id"],
                occurred_at=occurred, recorded_at=recorded, evidence=evidence)
        self.assertEqual(sent["status"], "manual_send_recorded")
        record = self.record()
        self.assertEqual(record["submissions"], self.original["submissions"])
        self.assertEqual(record["events"], self.original["events"])
        send = record["outreach"][0]["manual_sends"][0]
        self.assertEqual(send["occurred_at"], occurred.isoformat())
        self.assertEqual(send["recorded_at"], recorded.isoformat())
        self.assertEqual(send["evidence_sha256"], w.digest(evidence))

    def test_settings_schema_strictly_validates_optional_outreach(self):
        self.assertEqual(settings.validate_settings({})["outreach"], outreach.DEFAULT_POLICY)
        for bad in (None, False, [], {"unknown": True}, {"enabled": 1},
                    {"business_days": True}, {"daily_draft_cap": -1}, {"do_not_contact": ["\n"]}):
            with self.subTest(bad=bad), self.assertRaises(settings.SettingsError):
                settings.validate_settings({"outreach": bad})


if __name__ == "__main__":
    unittest.main()
