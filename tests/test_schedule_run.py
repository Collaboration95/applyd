"""Temporary synthetic workspaces and real runner gates; no live providers or OS notices."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import schedule_run as s
import settings
import setup_workspace
import workspace as w
from runner import Kind, Observation


NOW = datetime.fromisoformat("2026-10-20T09:00:00+08:00")
CONFIG = {"schema_version": 1, "enabled": True, "id": "fixture", "weekdays": [0, 1, 2, 3, 4],
          "start": "09:00", "end": "09:15", "timezone": None, "target": 10, "max_steps": 100}
PRIVATE = "SYNTHETIC_PRIVATE_PROVIDER_CANARY"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FixtureAdapter:
    """Stub UI observations; preparation, review, claims and confirmation use real workspace code."""
    def __init__(self, case):
        self.case = case
        self.calls = []
        self.clicks = 0

    def observe_url(self, action):
        return self.case.record(action.application_id)["listing_url"]

    def __call__(self, action):
        case = self.case
        now = case.clock()
        self.calls.append(action.kind)
        if action.kind == Kind.DISCOVER:
            index = len(w.records(case.root)) + 1
            w.capture(case.root, case.job(index, action.run_id), now, deferred=True)
        elif action.kind == Kind.PREPARE:
            result = w.prepare_answers(case.root, action.application_id, [], now, deferred=True)
            case.event(action.application_id, "ready", resume_scope="backend", answers=case.root / result["answers_path"])
        elif action.kind == Kind.REVIEW:
            prepared = case.record(action.application_id)["prepared"]
            w.review_form(case.root, action.application_id, {
                "attempt_id": prepared["attempt_id"], "resume_sha256": prepared["sha256"], "fields": []}, now, deferred=True)
        elif action.kind == Kind.SUBMIT:
            self.clicks += 1
            evidence = case.root / "applications/evidence/fixture-confirmation.txt"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("Synthetic observed confirmation; no employer action")
            case.event(action.application_id, "submitted", evidence=evidence)
        return Observation()


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        setup_workspace.init_workspace(self.root)
        self.clock = Clock()
        self.config = {**CONFIG, "weekdays": list(CONFIG["weekdays"])}
        self.policy = {"mode": "apply", "timezone": "Asia/Singapore", "per_run_cap": 3,
                       "daily_cap": 5, "per_run_minutes": 5, "resume_scope": "backend", "easy_apply_only": True}
        self.configure()
        releases = []
        for identity, scope, purpose, parent in (("master-v1", "master", "reference", None),
                                                  ("backend-v1", "backend", "application", "master-v1")):
            path = self.root / f"resumes/{identity}{'.md' if purpose == 'reference' else '.pdf'}"
            path.write_text("Synthetic fixture resume")
            releases.append({"id": identity, "scope": scope, "purpose": purpose, "status": "active",
                             "path": str(path.relative_to(self.root)), "sha256": w.digest(path),
                             "released_at": (NOW - timedelta(days=1)).isoformat(),
                             "effective_from": (NOW - timedelta(days=1)).isoformat(),
                             "review_due_at": (NOW + timedelta(days=30)).isoformat(),
                             "valid_until": None, "source_master_id": parent, "selected_projects": []})
        w.atomic_json(self.root / "resumes/registry.json", {"releases": releases})
        self.adapter = FixtureAdapter(self)
        self.notify = Mock()
        # This existing legacy hook is never allowed to cause a real OS action.
        self.legacy_notice = self.enterContext(patch.object(s.safety, "local_notification"))
        self.native_cleanup = self.enterContext(patch.object(s.cleanup, "BraveAppleScriptAdapter",
                                                            side_effect=AssertionError("No implicit native adapter")))

    def configure(self, **changes):
        self.policy.update(changes)
        w.atomic_json(self.root / "settings.json", self.policy)

    @staticmethod
    def job(index, run_id):
        return {"source": "linkedin_job_page", "company": "Synthetic Company", "role": f"Engineer {index}",
                "location": "Singapore", "platform": "linkedin", "platform_job_id": str(70000 + index),
                "listing_url": f"https://www.linkedin.com/jobs/view/{70000 + index}",
                "job_description": "Synthetic full listing. No real employer.", "easy_apply": True,
                "run_id": run_id, "batch_id": run_id}

    def event(self, identity, status, **extra):
        args = argparse.Namespace(id=identity, status=status, note="", resume_scope=None,
                                  answers=None, evidence=None)
        for key, value in extra.items():
            setattr(args, key, value)
        return w.event(self.root, args, self.clock(), deferred=True)

    def record(self, identity):
        return w.read(self.root / f"applications/records/{identity}/record.json")

    def tick(self, **kwargs):
        return s.tick(self.root, self.config, clock=self.clock, **kwargs)

    def summary(self, result):
        return w.read(self.root / f"applications/runs/{result['run_id']}/schedule-summary.json")

    def test_real_runner_caps_target_and_normal_finalization(self):
        result = self.tick(adapter=self.adapter, notify=self.notify)
        self.assertEqual(result["reason"], "quota_reached")
        self.assertEqual(self.adapter.clicks, 3)
        self.assertEqual(self.adapter.calls.count(Kind.DISCOVER), 3)
        self.assertEqual(self.summary(result)["cleanup"], "planned")
        self.assertEqual(self.summary(result)["export"], "exported")
        self.assertEqual(self.summary(result)["notification"], {"status": "delivered"})
        payload = self.notify.call_args.args[0]
        self.assertEqual(payload, {"summary_uri": result["summary_uri"], "waiting_questions": 0})
        self.assertTrue(payload["summary_uri"].startswith("file:///"))
        self.legacy_notice.assert_not_called()

    def test_target_only_tightens_and_daily_quota_counts_prior_run(self):
        self.config["target"] = 1
        self.configure(daily_cap=1)
        first = self.tick(adapter=self.adapter)
        self.assertEqual(self.adapter.clicks, 1)
        self.config["id"] = "second"
        self.config["target"] = 99
        other = Mock()
        second = self.tick(adapter=other)
        self.assertEqual(second["reason"], "quota_reached")
        other.assert_not_called()
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_zero_caps_allow_no_adapter_action(self):
        for field in ("per_run_cap", "daily_cap", "per_run_minutes"):
            with self.subTest(field=field):
                self.config["id"] = field.replace("_", "-")
                self.configure(**{"per_run_cap": 3, "daily_cap": 5, "per_run_minutes": 5, field: 0})
                adapter = Mock()
                result = self.tick(adapter=adapter)
                self.assertIn(result["reason"], {"quota_reached", "deadline_reached"})
                adapter.assert_not_called()

    def test_deadline_stops_after_verified_discovery_and_records_actual_end(self):
        self.configure(per_run_minutes=1)
        def adapter(action):
            result = self.adapter(action)
            self.clock.now += timedelta(minutes=1)
            return result
        result = self.tick(adapter=adapter, notify=self.notify)
        self.assertEqual(result["reason"], "deadline_reached")
        self.assertEqual(self.adapter.calls, [Kind.DISCOVER])
        self.assertEqual(self.adapter.clicks, 0)
        self.assertEqual(self.summary(result)["ended_at"], self.clock().isoformat())
        self.assertTrue((self.root / f"applications/runs/{result['run_id']}/summary.md").exists())

    def test_real_work_budget_and_schedule_step_limit(self):
        self.configure(work_budget={"max_actions": 1, "max_retries": 0})
        result = self.tick(adapter=self.adapter)
        self.assertEqual(result["reason"], "action_budget_reached")
        self.assertEqual(self.adapter.calls, [Kind.DISCOVER])
        self.config["id"] = "steps"
        self.config["max_steps"] = 1
        self.configure(work_budget={"max_actions": 10})
        result = self.tick(adapter=self.adapter)
        self.assertEqual(result["reason"], "step_limit_reached")
        self.assertEqual(len(self.adapter.calls), 2)

    def test_unbounded_settings_refused_before_run_or_adapter(self):
        for field in ("per_run_cap", "daily_cap", "per_run_minutes"):
            with self.subTest(field=field):
                self.config["id"] = field.replace("_", "-")
                self.configure(**{"per_run_cap": 3, "daily_cap": 5, "per_run_minutes": 5, field: None})
                result = self.tick(adapter=self.adapter)
                self.assertEqual(result["reason"], "unbounded_settings")
                self.assertFalse((self.root / f"applications/runs/{result['run_id']}/run.json").exists())
        self.assertEqual(self.adapter.calls, [])

    def test_due_window_weekday_timezone_inheritance_and_no_catchup(self):
        policy = settings.load_settings(self.root)
        cases = (("2026-10-20T08:59:59+08:00", False), ("2026-10-20T09:00:00+08:00", True),
                 ("2026-10-20T09:14:59+08:00", True), ("2026-10-20T09:15:00+08:00", False),
                 ("2026-10-24T09:01:00+08:00", False), ("2026-10-20T01:01:00+00:00", True))
        for stamp, due in cases:
            with self.subTest(stamp=stamp):
                self.assertEqual(s.due_slot(self.config, policy, datetime.fromisoformat(stamp)) is not None, due)
        self.config["timezone"] = "UTC"
        self.assertIsNone(s.due_slot(self.config, policy, NOW))
        self.config["enabled"] = False
        self.assertEqual(self.tick()["reason"], "not_due")
        self.assertFalse((self.root / "applications/schedule/slots").exists())

    def test_dst_fold_cannot_create_second_slot(self):
        config = {**self.config, "timezone": "America/New_York", "weekdays": [6], "start": "01:00", "end": "02:00"}
        policy = settings.load_settings(self.root)
        first = s.due_slot(config, policy, datetime.fromisoformat("2026-11-01T01:30:00-04:00"))
        second = s.due_slot(config, policy, datetime.fromisoformat("2026-11-01T01:30:00-05:00"))
        self.assertEqual(first, second)

    def test_duplicate_and_config_edits_never_replay_or_renotify(self):
        first = self.tick(adapter=self.adapter, notify=self.notify)
        calls = list(self.adapter.calls)
        self.config.update(target=99, start="08:59")
        second = self.tick(adapter=self.adapter, notify=self.notify)
        self.assertEqual(second["reason"], "slot_consumed")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.adapter.calls, calls)
        self.notify.assert_called_once()

    def test_concurrent_ticks_across_schedule_ids_have_one_writer(self):
        entered, release = Event(), Event()
        def adapter(action):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("Fixture timed out")
            return self.adapter(action)
        self.config["max_steps"] = 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.tick, adapter=adapter)
            self.assertTrue(entered.wait(timeout=5))
            try:
                with self.assertRaisesRegex(s.ScheduleError, "^schedule_busy$"):
                    s.tick(self.root, {**self.config, "id": "competing"}, clock=self.clock, adapter=Mock())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10)["reason"], "step_limit_reached")
        self.assertEqual(len(list((self.root / "applications/schedule/slots").glob("*.json"))), 1)

    def test_stop_prevents_run_snapshot_and_any_adapter_call(self):
        (self.root / s.safety.STOP).touch()
        result = self.tick(adapter=self.adapter, notify=self.notify)
        self.assertEqual(result["reason"], "manual_stop")
        self.assertEqual(self.adapter.calls, [])
        self.assertFalse((self.root / f"applications/runs/{result['run_id']}/run.json").exists())
        self.notify.assert_not_called()
        self.legacy_notice.assert_not_called()

    def test_unresolved_account_safety_ledger_without_sentinel_blocks(self):
        evidence = self.root / "applications/evidence/fixture.png"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(b"synthetic screenshot fixture")
        s.safety.stop_run(self.root, "prior", "account_safety", NOW,
                          {"screenshot": "applications/evidence/fixture.png"})
        (self.root / s.safety.SENTINEL).unlink()
        result = self.tick(adapter=self.adapter)
        self.assertEqual(result["reason"], "account_safety")
        self.assertFalse((self.root / f"applications/runs/{result['run_id']}/run.json").exists())
        self.assertEqual(self.adapter.calls, [])
        self.legacy_notice.assert_not_called()

    def test_stop_between_steps_finalizes_without_os_action(self):
        def adapter(action):
            result = self.adapter(action)
            (self.root / s.safety.STOP).touch()
            return result
        result = self.tick(adapter=adapter, notify=self.notify)
        self.assertEqual(result["reason"], "manual_stop")
        self.assertEqual(self.adapter.calls, [Kind.DISCOVER])
        self.assertEqual(self.summary(result)["cleanup"], "planned")
        self.notify.assert_called_once()
        self.legacy_notice.assert_not_called()

    def test_adapter_missing_typed_handoff_and_dry_run_never_calls_adapters(self):
        missing = self.tick(notify=self.notify)
        self.assertEqual(missing["reason"], "adapter_missing")
        state = w.read(self.root / f"applications/runs/{missing['run_id']}/state.json")
        self.assertIsNone(state["pending"])
        self.assertIsNone(state["checkpoint"])
        self.config["id"] = "dry"
        adapter, close = Mock(), Mock()
        result = self.tick(dry_run=True, adapter=adapter, cleanup_adapter=close, execute_cleanup=True)
        self.assertEqual(result["reason"], "dry_run_planned")
        self.assertEqual(self.summary(result)["cleanup"], "planned")
        adapter.assert_not_called()
        close.enumerate_tabs.assert_not_called()
        self.native_cleanup.assert_not_called()

    def test_adapter_failure_private_text_not_persisted_and_pending_blocks_new_slot(self):
        adapter = Mock(side_effect=RuntimeError(PRIVATE))
        result = self.tick(adapter=adapter, notify=self.notify)
        self.assertEqual(result["reason"], "unsupported")
        self.assertIsNotNone(w.read(self.root / f"applications/runs/{result['run_id']}/state.json")["pending"])
        self.clock.now += timedelta(days=1)
        second = self.tick(adapter=adapter)
        self.assertEqual(second["reason"], "reconciliation_required")
        self.assertFalse((self.root / f"applications/runs/{second['run_id']}/run.json").exists())
        adapter.assert_called_once()
        for path in (self.root / "applications").rglob("*.json"):
            self.assertNotIn(PRIVATE, path.read_text())

    def test_cleanup_notification_and_export_failures_leave_summary_and_no_replay(self):
        close = Mock()
        close.enumerate_tabs.side_effect = RuntimeError(PRIVATE)
        notify = Mock(side_effect=RuntimeError(PRIVATE))
        with patch.object(w, "export_csv", side_effect=RuntimeError(PRIVATE)):
            result = self.tick(cleanup_adapter=close, execute_cleanup=True, notify=notify)
        summary = self.summary(result)
        self.assertEqual(summary["cleanup"], "cleanup_failed")
        self.assertEqual(summary["export"], "export_failed")
        self.assertEqual(summary["notification"], {"status": "failed", "reason": "notification_failed"})
        self.assertEqual(summary["waiting_questions"], 0)
        self.assertNotIn(PRIVATE, json.dumps(summary))
        self.assertEqual(self.tick(notify=notify)["reason"], "slot_consumed")
        notify.assert_called_once()

    def test_cleanup_requires_explicit_adapter_and_never_falls_back_to_native(self):
        result = self.tick(execute_cleanup=True)
        self.assertEqual(self.summary(result)["cleanup"], "cleanup_failed")
        self.native_cleanup.assert_not_called()
        close = Mock()
        close.enumerate_tabs.return_value = []
        self.config["id"] = "authorized-cleanup"
        result = self.tick(execute_cleanup=True, cleanup_adapter=close)
        self.assertEqual(self.summary(result)["cleanup"], "executed")
        close.enumerate_tabs.assert_called_once()

    def test_interrupt_still_finalizes_and_notifies(self):
        adapter = Mock(side_effect=KeyboardInterrupt())
        result = self.tick(adapter=adapter, notify=self.notify)
        self.assertEqual(result["reason"], "run_interrupted")
        self.assertEqual(self.summary(result)["cleanup"], "planned")
        self.notify.assert_called_once()
        self.assertEqual(self.tick(adapter=adapter)["reason"], "slot_consumed")
        adapter.assert_called_once()

    def test_hard_process_exit_recovery_is_plan_only_and_never_replays(self):
        config = self.root / "schedule.json"
        w.atomic_json(config, self.config)
        code = """import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import schedule_run as s
from datetime import datetime
now = datetime.fromisoformat('2026-10-20T09:00:00+08:00')
def crash(action): os._exit(73)
s.tick(Path(sys.argv[2]), Path(sys.argv[3]), clock=lambda: now, adapter=crash)
"""
        process = subprocess.run([sys.executable, "-c", code, str(Path(s.__file__).parent),
                                  str(self.root), str(config)], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 73, process.stderr)
        result = self.tick(adapter=self.adapter, cleanup_adapter=Mock(), execute_cleanup=True, notify=self.notify)
        self.assertEqual(result["status"], "manual_handoff")
        self.assertEqual(result["reason"], "reconciliation_required")
        self.assertEqual(self.summary(result)["cleanup"], "planned")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.tick(adapter=self.adapter, notify=self.notify)["reason"], "slot_consumed")
        self.notify.assert_called_once()

    def test_interrupted_submit_retains_uncertainty_and_never_replays_click(self):
        class Interrupted(FixtureAdapter):
            def __call__(adapter, action):
                if action.kind == Kind.SUBMIT:
                    adapter.clicks += 1
                    self.assertEqual(self.record(action.application_id)["status"], "submission_uncertain")
                    raise SystemExit()
                return super().__call__(action)
        adapter = Interrupted(self)
        result = self.tick(adapter=adapter, notify=self.notify)
        self.assertEqual(result["reason"], "run_interrupted")
        self.assertEqual(adapter.clicks, 1)
        state = w.read(self.root / f"applications/runs/{result['run_id']}/state.json")
        self.assertEqual(state["pending"]["action"]["kind"], "submit")
        self.assertEqual(self.record(state["pending"]["action"]["application_id"])["status"], "submission_uncertain")
        self.assertEqual(self.tick(adapter=adapter)["reason"], "slot_consumed")
        self.clock.now += timedelta(days=1)
        self.assertEqual(self.tick(adapter=adapter)["reason"], "reconciliation_required")
        self.assertEqual(adapter.clicks, 1)

    def test_claim_record_crash_window_recovers_before_executing_cleanup(self):
        close = Mock()
        def adapter(action):
            result = self.adapter(action)
            if action.kind == Kind.REVIEW:
                url = self.record(action.application_id)["listing_url"]
                s.cleanup.register_tab(self.root, action.run_id, 1, 11, url, action.application_id, NOW)
                close.enumerate_tabs.return_value = [{"window_id": 1, "tab_id": 11, "url": url}]
            return result
        adapter.observe_url = self.adapter.observe_url
        def interrupted_claim(root, application_id, observed_url, session_id, now, deferred=False):
            saved = self.record(application_id)
            w.atomic_json(root / "applications/browser-claim.json", {
                "application_id": application_id, "attempt_id": saved["prepared"]["attempt_id"],
                "session_id": session_id, "observed_url": observed_url, "occurred_at": now.isoformat()})
            raise SystemExit()
        with patch.object(w, "begin_submit", side_effect=interrupted_claim):
            result = self.tick(adapter=adapter, cleanup_adapter=close, execute_cleanup=True)
        self.assertEqual(self.summary(result)["submit_recovery"], "recovered")
        self.assertEqual(w.records(self.root)[0]["status"], "submission_uncertain")
        decision = w.read(self.root / f"applications/runs/{result['run_id']}/cleanup.json")["decisions"][0]
        self.assertEqual(decision["reason"], "submission_uncertain")
        close.close_tab.assert_not_called()

    def test_interrupted_notification_intent_is_not_replayed_on_recovery(self):
        run_id = s.due_slot(self.config, settings.load_settings(self.root), NOW)
        receipt = {"schema_version": 1, "run_id": run_id, "phase": "finalizing", "dry_run": False,
                   "reason": "run_failed", "notification": {"status": "attempting"}}
        w.atomic_json(self.root / s._receipt_ref(run_id), receipt)
        result = self.tick(notify=self.notify)
        self.assertEqual(result["reason"], "reconciliation_required")
        self.assertEqual(self.summary(result)["notification"], {"status": "attempting"})
        self.notify.assert_not_called()

    def test_configs_readonly_and_frozen_run_cannot_be_relaxed(self):
        self.configure(per_run_cap=1)
        config = self.root / "schedule.json"
        w.atomic_json(config, self.config)
        original = config.read_bytes()
        config.chmod(0o444)
        frozen = []
        def adapter(action):
            path = self.root / f"applications/runs/{action.run_id}/run.json"
            frozen.append(path.read_bytes())
            self.configure(per_run_cap=99, daily_cap=99, per_run_minutes=99)
            return self.adapter(action)
        adapter.observe_url = self.adapter.observe_url
        result = s.tick(self.root, config, clock=self.clock, adapter=adapter)
        self.assertEqual(result["reason"], "quota_reached")
        self.assertEqual(self.adapter.clicks, 1)
        self.assertEqual(len(set(frozen)), 1)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(settings.load_run(self.root, result["run_id"])["settings"]["per_run_cap"], 1)

    def test_waiting_count_canonical_shared_questions_and_fresh_resolution(self):
        # Capture two fixture forms outside a run so no schedule adapter is needed.
        for index in (1, 2):
            job = self.job(index, None)
            job.pop("run_id")
            job.pop("batch_id")
            app = w.capture(self.root, job, NOW, deferred=True)["application_id"]
            w.prepare_answers(self.root, app, [{"question": "Synthetic shared qualification?"},
                                             {"question": f"Synthetic unique qualification {index}?"}], NOW, deferred=True)
        self.assertEqual(s.waiting_questions(self.root, NOW), 3)
        w.atomic_json(self.root / "profile/answers.json", {"schema_version": 1, "answers": [{
            "id": "fixture-answer", "value": "Yes", "type": "text", "scope": {},
            "aliases": ["Synthetic shared qualification?"], "source": "Synthetic explicit user",
            "confirmed_at": NOW.isoformat()}]})
        self.assertEqual(s.waiting_questions(self.root, NOW), 2)
        result = self.tick(dry_run=True, notify=self.notify)
        self.assertEqual(self.notify.call_args.args[0]["waiting_questions"], 2)
        self.assertEqual(self.summary(result)["waiting_questions"], 2)

    def test_question_count_failure_stays_unknown_and_notification_runs(self):
        with patch.object(s, "waiting_questions", side_effect=RuntimeError(PRIVATE)):
            result = self.tick(notify=self.notify)
        self.assertIsNone(self.summary(result)["waiting_questions"])
        self.assertEqual(self.summary(result)["question_count"], "question_count_failed")
        self.notify.assert_called_once()

    def test_strict_schema_and_json_fail_closed(self):
        invalid = ({"enabled": False}, {**self.config, "extra": 1}, {**self.config, "enabled": 1},
                   {**self.config, "id": "../escape"}, {**self.config, "weekdays": [True]},
                   {**self.config, "weekdays": [0, 0]}, {**self.config, "start": "9:00"},
                   {**self.config, "end": "09:00"}, {**self.config, "timezone": "Bad/Zone"},
                   {**self.config, "target": True}, {**self.config, "max_steps": 101})
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(s.ScheduleError):
                s.validate_schedule(config)
        path = self.root / "schedule.json"
        for text in ('{"enabled":true,"enabled":false}', '{"target":NaN}', '[]', PRIVATE):
            path.write_text(text)
            with self.assertRaises(s.ScheduleError):
                s.load_schedule(path)
        self.assertFalse((self.root / "applications/schedule").exists())

    def test_escaping_schedule_artifact_is_rejected_without_external_writes(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "applications/schedule").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(s.ScheduleError, "^unsafe_artifact_path$"):
                self.tick()
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_local_notification_stub_uses_stdin_only_and_no_shell(self):
        payload = {"summary_uri": "file:///synthetic/private/summary.json", "waiting_questions": 2}
        with patch.object(s.sys, "platform", "darwin"), patch.object(s.subprocess, "run") as run:
            s.local_notification(payload)
        args, kwargs = run.call_args
        self.assertEqual(args, (["osascript", "-"],))
        self.assertNotIn("shell", kwargs)
        self.assertIn(payload["summary_uri"], kwargs["input"])
        self.assertIn("Questions waiting: 2", kwargs["input"])
        self.assertEqual(kwargs["timeout"], 10)

    def test_default_tick_never_invokes_subprocess_or_installs_background_work(self):
        with patch.object(s.subprocess, "run") as run, patch.object(s.subprocess, "Popen") as popen:
            self.tick(dry_run=True)
        run.assert_not_called()
        popen.assert_not_called()

    def test_cli_notification_flag_is_explicit_and_uses_stub_only(self):
        config = self.root / "fixture-schedule.json"
        w.atomic_json(config, self.config)
        with patch.object(s, "local_notification") as notice, redirect_stdout(io.StringIO()):
            result = s.main(["--workspace", str(self.root), "--config", str(config),
                             "--dry-run", "--now", NOW.isoformat(), "--notify-local"])
        self.assertEqual(result, 0)
        notice.assert_called_once()
        self.assertEqual(notice.call_args.args[0]["waiting_questions"], 0)
        self.assertIn("schedule-summary.json", notice.call_args.args[0]["summary_uri"])

    def test_notification_callback_runs_after_workspace_writer_released(self):
        import fcntl
        def notify(_):
            with (self.root / ".workspace.lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = self.tick(notify=notify)
        self.assertEqual(self.summary(result)["notification"]["status"], "delivered")

    def test_cli_synthetic_smoke_no_install_browser_or_notifications(self):
        config = self.root / "fixture-schedule.json"
        w.atomic_json(config, self.config)
        process = subprocess.run([sys.executable, str(Path(s.__file__)), "--workspace", str(self.root),
                                  "--config", str(config), "--dry-run", "--now", NOW.isoformat()],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {"status": "finished", "reason": "dry_run_planned", "waiting_questions": 0})
        self.assertEqual(process.stderr, "")
        self.assertNotIn(str(self.root), process.stdout)
        self.assertEqual({p.name for p in self.root.iterdir()} - {
            "applications", "dashboard", "profile", "projects", "resumes", "settings.json",
            "fixture-schedule.json", ".workspace.lock"}, set())
        receipt = next((self.root / "applications/schedule/slots").glob("*.json"))
        self.assertEqual(w.read(receipt)["notification"]["status"], "not_requested")

    def test_cli_provider_failure_and_invalid_input_do_not_leak(self):
        config = self.root / "fixture-schedule.json"
        w.atomic_json(config, self.config)
        output = io.StringIO()
        real_tick = s.tick
        def fixture_tick(root, config, **kwargs):
            return real_tick(root, config, **{**kwargs, "clock": self.clock})
        with patch.object(s, "tick", side_effect=fixture_tick), patch.object(s, "build_runner", side_effect=RuntimeError(PRIVATE)), redirect_stdout(output):
            code = s.main(["--workspace", str(self.root), "--config", str(config)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "run_failed")
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        config.write_text(PRIVATE)
        output = io.StringIO()
        with redirect_stdout(output):
            code = s.main(["--workspace", str(self.root), "--config", str(config)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "invalid_json")
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()
