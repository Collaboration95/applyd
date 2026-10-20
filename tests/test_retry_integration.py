"""Synthetic private workspaces, fake clocks and local CLI processes only."""

import csv
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow as fixtures
import workspace as w
import retry_policy as retry


class RetryIntegrationTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    add_release = fixtures.WorkflowTests.add_release
    save_registry = fixtures.WorkflowTests.save_registry
    job = fixtures.WorkflowTests.job
    capture = fixtures.WorkflowTests.capture
    event = fixtures.WorkflowTests.event
    ready = fixtures.WorkflowTests.ready
    record = fixtures.WorkflowTests.record
    evidence = fixtures.WorkflowTests.evidence

    def save(self, app, **changes):
        record = self.record(app)
        record.update(changes)
        w.atomic_json(self.root / "applications/records" / app / "record.json", record)
        return record

    def fail(self, app, reason="timeout"):
        return w.retry_failure(self.root, app, reason, self.now)

    def deadline(self, app):
        self.now = w.timestamp(self.record(app)["next_eligible_at"])

    def resume(self, app, blocker_id=None):
        blocker_id = blocker_id or self.record(app)["browser_stop"]["id"]
        return w.retry_resume(self.root, app, blocker_id, self.evidence(), self.now)

    def other(self):
        return w.capture(self.root, self.job(company="Independent Co", platform_job_id="999999",
                                           listing_url="https://www.linkedin.com/jobs/view/999999"), self.now)["application_id"]

    def cli(self, *args):
        return subprocess.run([sys.executable, str(Path(w.__file__).resolve()), "--workspace", str(self.root), *args],
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, text=True,
                              capture_output=True, timeout=10)

    def unknown(self):
        app = self.capture()
        w.atomic_json(self.root / "settings.json", {"resume_scope": "backend"})
        w.atomic_json(self.root / "profile/answers.json", {"schema_version": 1, "answers": []})
        questions = [{"question": "Synthetic qualification A?"}, {"question": "Synthetic qualification B?"}]
        result = w.prepare_answers(self.root, app, questions, self.now)
        return app, result

    def confirm_answer(self, question):
        path = self.root / "profile/answers.json"
        bank = w.read(path)
        bank["answers"].append({"id": "answer-" + str(len(bank["answers"])), "value": "Yes", "type": "text",
                                "scope": {}, "aliases": [question], "source": "Synthetic user confirmation",
                                "confirmed_at": self.now.isoformat()})
        w.atomic_json(path, bank)

    def test_fixed_combined_budget_exhausts_across_cli_restart(self):
        app = self.capture()
        for number, reason in enumerate(("timeout", "throttling", "timeout", "throttling"), 1):
            self.fail(app, reason)
            self.deadline(app)
            decision = retry.check_retry(self.record(app), self.now)
            self.assertEqual(decision.decision, "eligible" if number <= 3 else "exhausted")
        result = self.cli("retry-status", "--id", app)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "exhausted")
        self.assertEqual(self.record(app)["retry_budget"], 3)
        with self.assertRaisesRegex(ValueError, "exhausted"):
            self.event(app, "preparing")

    def test_backoff_denies_prepare_ready_review_and_preflight_without_refreezing(self):
        app = self.ready()
        prepared = self.record(app)["prepared"]
        exact = {path: (self.root / prepared[path]).read_bytes() for path in ("exact_resume_copy", "answers_path", "review_snapshot")}
        self.fail(app)
        attempts = list((self.root / "applications/records" / app / "submissions").iterdir())
        for operation in (lambda: self.event(app, "preparing"),
                          lambda: self.event(app, "ready", resume_scope="backend", answers=self.root / prepared["answers_path"]),
                          lambda: w.prepare_answers(self.root, app, [], self.now),
                          lambda: w.review_form(self.root, app, {}, self.now),
                          lambda: w.preflight(self.root, {**self.record(app), "status": "ready"}, self.now)):
            with self.assertRaisesRegex(ValueError, "backoff"):
                operation()
        self.assertEqual(attempts, list((self.root / "applications/records" / app / "submissions").iterdir()))
        self.assertEqual(self.record(app)["prepared"], prepared)
        for key, content in exact.items():
            self.assertEqual((self.root / prepared[key]).read_bytes(), content)
        self.deadline(app)
        self.event(app, "preparing")

    def test_status_reads_and_repeated_failure_do_not_extend_wait_or_export(self):
        app = self.capture()
        self.fail(app)
        before = self.record(app)
        with patch.object(w, "export_csv", side_effect=AssertionError("unexpected export")):
            for _ in range(3):
                self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "backoff")
                self.assertTrue(self.fail(app)["already_recorded"])
        self.assertEqual(self.record(app), before)

    def test_independent_job_can_continue_during_backoff_and_exhaustion(self):
        app = self.capture()
        other = self.other()
        for _ in range(4):
            self.fail(app)
            self.deadline(app)
        w.work_guard(self.root, "independent", self.now, "browser-guard")
        self.event(other, "preparing")

    def test_login_stops_all_browser_boundaries_and_runs_after_restart(self):
        app = self.ready()
        other = self.other()
        self.fail(app, "login")
        self.now += timedelta(days=1)
        for command in ("browser-guard", "run-start", "scope-check", "register-tab", "safety-check", "preflight"):
            with self.assertRaisesRegex(ValueError, "retry_browser_stopped"):
                w.work_guard(self.root, "a-new-run", self.now, command)
        with self.assertRaisesRegex(ValueError, "retry_browser_stopped"):
            self.event(other, "preparing")
        process = self.cli("browser-guard", "--run-id", "a-new-run")
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("retry_browser_stopped", process.stderr)
        self.assertFalse((self.root / "applications/runs/a-new-run/run.json").exists())

    def test_capture_duplicate_and_new_capture_never_scan_during_browser_stop(self):
        app = self.capture()
        self.fail(app, "page_changed")
        # Export normally scans; deferred mutation isolates the capture path.
        with patch.object(w, "records", side_effect=AssertionError("record scan")):
            self.assertTrue(w.capture(self.root, self.job(), self.now, deferred=True)["duplicate"])
            self.assertFalse(w.capture(self.root, self.job(company="Another", platform_job_id="998",
                                                            listing_url="https://www.linkedin.com/jobs/view/998"),
                                       self.now, deferred=True)["duplicate"])

    def test_resume_requires_saved_nonempty_evidence_and_current_incident(self):
        app = self.capture()
        self.fail(app, "page_changed")
        incident = self.record(app)["browser_stop"]["id"]
        before = self.record(app)
        empty = self.root / "empty.txt"
        empty.write_text(" \n\t")
        for blocker, evidence in (("retry-stale", self.evidence()), (incident, empty), (incident, self.root / "missing")):
            with self.assertRaises((ValueError, OSError)):
                w.retry_resume(self.root, app, blocker, evidence, self.now)
            self.assertEqual(self.record(app), before)
        self.resume(app)
        resumed = self.record(app)
        self.assertEqual(resumed["retry_counts"], before["retry_counts"])
        self.assertEqual(resumed["blocker"]["resume"], resumed["browser_stop"]["resume"])
        self.assertEqual(resumed["events"][-1]["retry_blocker_id"], incident)
        saved = self.root / resumed["events"][-1]["evidence"]["path"]
        self.assertEqual(w.digest(saved), resumed["events"][-1]["evidence"]["sha256"])
        w.work_guard(self.root, "new", self.now, "browser-guard")
        self.fail(app, "login")
        with self.assertRaises(ValueError):
            self.resume(app, incident)

    def test_resume_cannot_predate_incident_or_use_modified_saved_evidence(self):
        app = self.capture()
        self.fail(app, "login")
        self.now -= timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "predates"):
            self.resume(app)
        self.now += timedelta(seconds=1)
        self.resume(app)
        saved = self.root / self.record(app)["events"][-1]["evidence"]["path"]
        saved.write_text("Synthetic tampered evidence")
        with self.assertRaisesRegex(ValueError, "evidence"):
            w.work_guard(self.root, "new", self.now, "browser-guard")

    def test_browser_resume_preserves_backoff_and_frozen_run_json(self):
        app = self.capture()
        w.settings.run_start(self.root, "synthetic-run", self.now)
        self.save(app, run_id="synthetic-run")
        run = self.root / "applications/runs/synthetic-run/run.json"
        frozen = run.read_bytes()
        self.fail(app, "timeout")
        deadline = self.record(app)["next_eligible_at"]
        self.fail(app, "login")
        self.resume(app)
        self.assertEqual(run.read_bytes(), frozen)
        self.assertEqual(self.record(app)["next_eligible_at"], deadline)
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "backoff")

    def test_two_pending_incidents_require_two_exact_resumes(self):
        app, other = self.capture(), self.other()
        self.fail(app, "login")
        self.fail(other, "page_changed")
        self.resume(app)
        with self.assertRaisesRegex(ValueError, "retry_browser_stopped"):
            w.work_guard(self.root, "new", self.now, "browser-guard")
        self.resume(other)
        w.work_guard(self.root, "new", self.now, "browser-guard")

    def test_unknown_ids_persist_and_partial_resolver_changes_do_not_requeue(self):
        app, prepared = self.unknown()
        original = self.record(app)
        self.assertEqual(original["blocker"]["answer_ids"], prepared["blocked_on_answer_ids"])
        self.event(app, "preparing")
        self.assertEqual(self.record(app)["status"], "awaiting_answer")
        with self.assertRaisesRegex(ValueError, "waiting_answer"):
            w.prepare_answers(self.root, app, [], self.now)
        self.confirm_answer("Unrelated synthetic question?")
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [])
        self.assertEqual(self.record(app)["blocked_on_answer_ids"], original["blocked_on_answer_ids"])
        self.confirm_answer("Synthetic qualification A?")
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [])
        partial = self.record(app)
        self.assertEqual(len(partial["blocked_on_answer_ids"]), 1)
        self.assertEqual(partial["blocker"]["answer_ids"], partial["blocked_on_answer_ids"])
        self.confirm_answer("Synthetic qualification B?")
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [app])
        self.assertEqual(self.record(app)["blocked_on_answer_ids"], [])
        self.assertEqual(self.record(app)["retry_counts"], original["retry_counts"])

    def test_answer_requeue_keeps_browser_stop_and_combined_failure_counts(self):
        app, _ = self.unknown()
        self.fail(app, "login")
        stopped = self.record(app)
        for question in ("Synthetic qualification A?", "Synthetic qualification B?"):
            self.confirm_answer(question)
        result = w.requeue_answers(self.root, self.now)
        self.assertEqual(result["runnable_ids"], [])
        self.assertEqual(self.record(app)["blocked_on_answer_ids"], [])
        self.assertEqual(self.record(app)["retry_counts"], stopped["retry_counts"])
        self.assertEqual(self.record(app)["browser_stop"], stopped["browser_stop"])
        self.resume(app)
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [app])

    def test_resolved_answers_do_not_reset_an_exhausted_budget(self):
        app, _ = self.unknown()
        # This state models a record imported after previous finite failures.
        record = self.record(app)
        record["retry_counts"]["timeout"] = 4
        self.save(app, retry_counts=record["retry_counts"])
        for question in ("Synthetic qualification A?", "Synthetic qualification B?"):
            self.confirm_answer(question)
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [])
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "exhausted")
        self.assertEqual(self.record(app)["retry_counts"]["timeout"], 4)

    def test_unrepresented_and_expired_answers_cannot_clear_actual_ids(self):
        app, _ = self.unknown()
        self.save(app, blocked_on_answer_ids=self.record(app)["blocked_on_answer_ids"] + ["unrepresented-id"])
        for question in ("Synthetic qualification A?", "Synthetic qualification B?"):
            self.confirm_answer(question)
        bank = w.read(self.root / "profile/answers.json")
        bank["answers"][0]["valid_until"] = self.now.isoformat()
        w.atomic_json(self.root / "profile/answers.json", bank)
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [])
        self.assertIn("unrepresented-id", self.record(app)["blocked_on_answer_ids"])
        self.assertEqual(len(self.record(app)["blocked_on_answer_ids"]), 2)

    def test_intent_uncertainty_survives_status_restore_and_requires_evidence(self):
        app = self.ready()
        w.begin_submit(self.root, app, self.job()["listing_url"], "synthetic-session", self.now)
        self.save(app, status="failed", submission_intent=None)
        self.fail(app, "timeout")
        self.assertEqual(self.record(app)["blocker"]["reason"], "submission_uncertain")
        self.assertNotIn("timeout", self.record(app)["retry_counts"])
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.event(app, "preparing")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.event(app, "failed")
        self.event(app, "failed", evidence=self.evidence(), note="Synthetic check confirms no submission")
        reconciled = self.record(app)
        self.assertIsNone(reconciled["blocker"])
        self.assertIn("reconciled_retry_blocker", reconciled["events"][-1])
        self.assertEqual(reconciled["retry_counts"]["submission_uncertain"], 1)
        self.event(app, "preparing")

    def test_browser_guard_recovers_crash_between_claim_and_record(self):
        app = self.ready()
        w.atomic_json(self.root / "applications/browser-claim.json", {"application_id": app, "attempt_id": "synthetic-crash"})
        with self.assertRaisesRegex(ValueError, "reconcile"):
            w.work_guard(self.root, "new", self.now, "browser-guard")
        self.assertEqual(self.record(app)["status"], "submission_uncertain")
        self.assertEqual(self.record(app)["blocker"]["reason"], "submission_uncertain")

    def test_uncertainty_does_not_clear_browser_stop_or_allow_resume_to_retry(self):
        app = self.ready()
        self.fail(app, "login")
        self.fail(app, "submission_uncertain")
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "reconciliation_required")
        self.assertTrue(w.retry_status(self.root, app, self.now)["browser_stops"])
        self.resume(app)
        w.work_guard(self.root, "new", self.now, "browser-guard")
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.event(app, "preparing")

    def test_login_observed_during_uncertainty_stops_session_without_reclassifying(self):
        app = self.ready()
        self.event(app, "submission_uncertain")
        self.fail(app, "login")
        self.assertEqual(self.record(app)["blocker"]["reason"], "submission_uncertain")
        self.assertEqual(self.record(app)["browser_stop"]["reason"], "login")
        self.resume(app)
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "reconciliation_required")

    def test_historical_confirmation_survives_session_stop_and_preserves_snapshot(self):
        app = self.ready()
        frozen = self.record(app)["prepared"]
        self.fail(app, "page_changed")
        self.event(app, "submission_uncertain")
        self.now += timedelta(days=40)
        self.event(app, "submitted", evidence=self.evidence())
        self.assertEqual(self.record(app)["submissions"][-1]["exact_resume_copy"], frozen["exact_resume_copy"])
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "confirmed")
        with self.assertRaisesRegex(ValueError, "retry_browser_stopped"):
            w.work_guard(self.root, "new", self.now, "browser-guard")
        self.resume(app)
        w.work_guard(self.root, "new", self.now, "browser-guard")
        with self.assertRaisesRegex(ValueError, "Confirmed"):
            self.event(app, "preparing")

    def test_exhausted_browser_stop_resume_permits_only_independent_work(self):
        app, other = self.capture(), self.other()
        for _ in range(3):
            self.fail(app, "login")
            self.resume(app)
        self.fail(app, "page_changed")
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "exhausted")
        fourth = self.record(app)["browser_stop"]["id"]
        self.resume(app)
        self.event(other, "preparing")
        with self.assertRaisesRegex(ValueError, "exhausted"):
            self.event(app, "preparing")
        self.fail(app, "login")
        self.assertNotEqual(self.record(app)["browser_stop"]["id"], fourth)
        with self.assertRaises(ValueError):
            self.resume(app, fourth)

    def test_record_index_and_exports_update_together_and_export_failure_is_recoverable(self):
        app = self.capture()
        result = self.fail(app)
        self.assertTrue(result["mutation"]["ok"])
        self.assertTrue(result["export"]["ok"])
        self.assertIn(" failed", (self.root / "applications/index/li-123456").read_text())
        rows = list(csv.DictReader((self.root / "applications/log.csv").read_text().splitlines()))
        self.assertEqual(rows[0]["status"], "failed")
        self.deadline(app)
        with patch.object(w, "export_csv", side_effect=OSError("Synthetic export failure")):
            result = self.fail(app)
        self.assertTrue(result["mutation"]["ok"])
        self.assertFalse(result["export"]["ok"])
        self.assertEqual(self.record(app)["retry_counts"]["timeout"], 2)
        self.assertTrue(w.export_state(self.root)["dirty"])
        self.assertTrue(w.recover(self.root, w.export_csv)["ok"])
        self.assertEqual(self.record(app)["retry_counts"]["timeout"], 2)

    def test_cli_failure_resume_status_and_guard_are_explicit_local_operations(self):
        app = self.capture()
        failure = self.cli("retry-failure", "--id", app, "--reason", "login")
        self.assertEqual(failure.returncode, 0, failure.stderr)
        incident = json.loads(failure.stdout)["blocker_id"]
        self.assertEqual(self.cli("browser-guard").returncode, 1)
        resume = self.cli("retry-resume", "--id", app, "--blocker-id", incident, "--evidence", str(self.evidence()))
        self.assertEqual(resume.returncode, 0, resume.stderr)
        self.assertTrue(json.loads(resume.stdout)["browser_resumed"])
        self.assertEqual(self.cli("browser-guard", "--id", app).returncode, 0)

    def test_terminal_scope_and_unsupported_failure_cannot_change_status_to_bypass(self):
        for reason in ("off_scope", "unsupported"):
            app = self.capture() if reason == "off_scope" else self.other()
            self.fail(app, reason)
            self.save(app, status="ready")
            with self.assertRaisesRegex(ValueError, "stopped"):
                w.preflight(self.root, self.record(app), self.now)

    def test_malformed_retry_or_browser_state_fails_closed_without_refreezing(self):
        app = self.capture()
        self.fail(app, "login")
        before = self.record(app)
        broken = dict(before)
        del broken["retry_budget"]
        w.atomic_json(self.root / "applications/records" / app / "record.json", broken)
        with self.assertRaises(ValueError):
            self.event(app, "preparing")
        self.save(app, **before)
        self.save(app, browser_stop={**before["browser_stop"], "resume": {}})
        with self.assertRaises(ValueError):
            w.work_guard(self.root, "new", self.now, "browser-guard")

    def test_stale_ready_record_cannot_bypass_persisted_backoff(self):
        app = self.ready()
        stale = self.record(app)
        self.fail(app)
        with self.assertRaisesRegex(ValueError, "backoff"):
            w.preflight(self.root, stale, self.now)

    def test_failed_events_classify_or_stop_without_buying_a_fresh_budget(self):
        app = self.capture()
        self.event(app, "preparing")
        self.event(app, "failed", reason="timeout", note="Synthetic timeout")
        self.assertEqual(self.record(app)["retry_counts"], {"timeout": 1})
        self.assertEqual(self.record(app)["events"][-1]["note"], "Synthetic timeout")
        self.deadline(app)
        self.event(app, "preparing")
        self.event(app, "failed", reason="throttling")
        self.assertEqual(self.record(app)["retry_counts"], {"timeout": 1, "throttling": 1})
        self.deadline(app)
        self.event(app, "preparing")
        self.event(app, "failed")
        self.assertEqual(w.retry_status(self.root, app, self.now)["decision"], "stopped")

    def test_index_failure_leaves_durable_record_and_dirty_recovery_marker(self):
        app = self.capture()
        with patch.object(w.seen_index, "update", side_effect=OSError("Synthetic index failure")):
            with self.assertRaises(OSError):
                self.fail(app, "login")
        self.assertTrue(w.export_state(self.root)["dirty"])
        self.assertEqual(self.record(app)["browser_stop"]["reason"], "login")
        with self.assertRaisesRegex(ValueError, "retry_browser_stopped"):
            w.work_guard(self.root, "new", self.now, "browser-guard")
        w.seen_index.reindex(self.root, w.records(self.root), w.canonical_url)
        self.assertTrue(w.recover(self.root, w.export_csv)["ok"])

    def test_legacy_policy_login_is_adopted_before_later_uncertainty(self):
        app = self.capture()
        state = retry.next_retry_state(self.record(app), "login", self.now)
        self.save(app, **state)
        self.fail(app, "login")
        self.assertEqual(self.record(app)["browser_stop"]["id"], state["blocker"]["id"])
        self.fail(app, "submission_uncertain")
        self.assertTrue(w.retry_status(self.root, app, self.now)["browser_stops"])

    def test_unknown_failure_codes_never_echo_provider_text(self):
        app = self.capture()
        private = "SYNTHETIC_PRIVATE_PROVIDER_TEXT"
        with self.assertRaises(ValueError) as caught:
            self.fail(app, private)
        self.assertNotIn(private, str(caught.exception))

    def test_scoped_answer_for_another_employer_cannot_release_dependency(self):
        app, _ = self.unknown()
        for question in ("Synthetic qualification A?", "Synthetic qualification B?"):
            self.confirm_answer(question)
        bank = w.read(self.root / "profile/answers.json")
        bank["answers"][0]["scope"] = {"company": "Unrelated Co"}
        w.atomic_json(self.root / "profile/answers.json", bank)
        self.assertEqual(w.requeue_answers(self.root, self.now)["runnable_ids"], [])
        self.assertEqual(len(self.record(app)["blocked_on_answer_ids"]), 1)

    def test_late_confirmation_preserves_claim_recovery_audit_and_counts(self):
        app = self.ready()
        w.atomic_json(self.root / "applications/browser-claim.json", {"application_id": app, "attempt_id": "synthetic-crash"})
        self.event(app, "submitted", evidence=self.evidence())
        record = self.record(app)
        self.assertEqual(record["status"], "submitted")
        self.assertEqual(record["retry_counts"]["submission_uncertain"], 1)
        self.assertTrue(any(e.get("reason") == "recovered_intent" for e in record["events"]))
        self.assertEqual(record["events"][-1]["reconciled_retry_blocker"]["reason"], "submission_uncertain")


if __name__ == "__main__":
    unittest.main()
