"""Focused runner tests. All workspaces, adapters and evidence are synthetic."""

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runner as r
import retry_policy
import settings
import workspace as w


NOW = datetime.fromisoformat("2026-10-20T09:00:00+08:00")


def job(app, **values):
    return {"application_id": app, "status": "discovered", "company": "Example Company",
            "role": "Engineer " + app, "location": "Example City", "platform": "linkedin",
            "listing_url": "https://example.invalid/jobs/" + app, "run_id": "run-test",
            "submissions": [], "blocked_on_answer_ids": [], **values}


def confirmation(**values):
    return {"status": "submitted", "submitted_at": NOW.isoformat(), "run_id": "run-test",
            "batch_id": "batch-test", "category": "job",
            "confirmation_path": "applications/evidence/confirmed.txt", **values}


class Parent:
    """Models the injected contract; never pretends to implement live gates."""
    def __init__(self, root):
        self.root = root
        self.locked = False
        self.calls = []
        self.target = 3
        self.categories = None
        self.ref = "applications/evidence/observed.txt"
        (root / "applications/evidence").mkdir(parents=True)
        (root / self.ref).write_text("Synthetic independently observed checkpoint")
        self.disposition = "verified"
        self.authorize = True

    def put(self, record):
        w.atomic_json(self.root / "applications/records" / record["application_id"] / "record.json", record)

    def get(self, app):
        return w.read(self.root / "applications/records" / app / "record.json")

    @contextmanager
    def writer(self):
        with w.writer(self.root):
            assert not self.locked
            self.locked = True
            try:
                yield
            finally:
                self.locked = False

    def recover(self, run_id, now):
        assert self.locked
        self.calls.append("recover")

    def queue(self, run_id, now):
        assert self.locked
        return r.Queue(w.records(self.root), "batch-test", self.target, self.categories)

    def guard(self, action, now):
        assert self.locked
        self.calls.append("guard")
        if action.application_id:
            saved = self.get(action.application_id)
            owned_submit = action.kind == r.Kind.SUBMIT and saved.get("submission_intent") == {"attempt_id": "synthetic"}
            if not owned_submit and retry_policy.check_retry(saved, now).decision.value != "eligible":
                raise ValueError("Synthetic gate refusal")

    def begin(self, action, now):
        assert self.locked
        self.calls.append("begin_submit")
        if self.authorize:
            record = self.get(action.application_id)
            record.update(status="submission_uncertain", submission_intent={"attempt_id": "synthetic"})
            self.put(record)
        return self.authorize

    def verify(self, action, observation, now):
        assert self.locked
        self.calls.append("verify")
        if self.disposition == "unresolved":
            return r.Verification("unresolved")
        if self.disposition == "not_executed":
            return r.Verification("not_executed", self.ref)
        if observation is None:
            # Restart may use only a durable result, never a cached UI assumption.
            marker = self.root / f"applications/evidence/result-{action.sequence}.json"
            if not marker.exists():
                return r.Verification("unresolved")
        if action.kind == r.Kind.PREPARE:
            record = self.get(action.application_id)
            record.update(status="ready", prepared={})
            self.put(record)
        elif action.kind == r.Kind.REVIEW:
            record = self.get(action.application_id)
            record["prepared"]["review_snapshot"] = self.ref
            self.put(record)
        elif action.kind == r.Kind.SUBMIT:
            record = self.get(action.application_id)
            record.update(status="submitted", submissions=[confirmation()], submission_intent=None)
            self.put(record)
        elif action.kind == r.Kind.DISCOVER:
            # A finite synthetic discovery result avoids an endless fake loop.
            self.put(job("new"))
        return r.Verification("verified", self.ref)

    def failure(self, action, reason, now):
        assert self.locked
        self.calls.append("failure:" + reason.value)
        if action.application_id:
            record = retry_policy.next_retry_state(self.get(action.application_id), reason, now)
            self.put(record)

    def adapter(self, action):
        assert self.locked
        assert self.calls[-1] == "guard"
        self.calls.append("adapter")
        w.atomic_json(self.root / f"applications/evidence/result-{action.sequence}.json", {"observed": True})
        return r.Observation(self.ref)

    def hooks(self):
        return r.Hooks(self.writer, self.recover, self.queue, self.guard, self.begin, self.verify, self.failure)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        w.atomic_json(self.root / "settings.json", {"timezone": "Asia/Singapore"})
        settings.run_start(self.root, "run-test", NOW)
        self.parent = Parent(self.root)

    def runner(self, adapter=True, hooks=None):
        return r.Runner(self.root, "run-test", hooks=hooks or self.parent.hooks(),
                        adapter=self.parent.adapter if adapter is True else adapter or None)

    def state(self):
        return w.read(self.root / "applications/runs/run-test/state.json")

    def execute(self, runner=None, steps=1):
        return (runner or self.runner()).execute(clock=lambda: NOW, max_steps=steps)

    def test_interrupted_run_blocked_confirmed_resolved_first_then_quota(self):
        blocked = retry_policy.next_retry_state(job("blocked"), "unknown_answer", NOW,
                                               blocked_answer_ids=["question-example"])
        blocked["status"] = "awaiting_answer"
        self.parent.put(blocked)
        self.parent.put(job("confirmed", status="ready", submissions=[confirmation()]))
        self.parent.put(job("fresh"))
        self.parent.target = 2
        self.assertEqual(self.runner().next_action(NOW).application_id, "fresh")
        self.assertIsNone(self.state()["checkpoint"])
        # The real parent resolver has now verified and persisted the answer.
        resolved = retry_policy.resolve_answer_blocker(blocked, ["question-example"], NOW)
        resolved.update(status="preparing", resumed_from_answers=True)
        self.parent.put(resolved)
        first = self.execute()[0]
        self.assertEqual((first.kind, first.application_id), (r.Kind.PREPARE, "blocked"))
        self.assertEqual(self.state()["checkpoint"]["action"]["kind"], "prepare")
        # A new runner/process reads saved records/checkpoint, not an old plan.
        second = self.runner().next_action(NOW)
        self.assertEqual((second.kind, second.application_id), (r.Kind.REVIEW, "blocked"))
        self.assertEqual(self.state()["checkpoint"]["action"]["kind"], "prepare")
        self.execute(steps=2)
        done = self.runner().next_action(NOW)
        self.assertEqual((done.kind, done.remaining), (r.Kind.COMPLETE, 0))
        self.assertEqual(self.parent.calls.count("begin_submit"), 1)
        self.assertEqual(self.parent.get("confirmed")["status"], "ready")

    def test_pending_submit_restart_never_clicks_again(self):
        self.parent.put(job("ready", status="ready", prepared={"review_snapshot": self.parent.ref}))
        def crash(action):
            raise SystemExit("Synthetic process exit")
        with self.assertRaises(SystemExit):
            self.execute(self.runner(adapter=crash))
        self.assertEqual(self.state()["pending"]["action"]["kind"], "submit")
        self.assertIsNone(self.state()["checkpoint"])
        for _ in range(2):
            action = self.runner().next_action(NOW)
            self.assertEqual((action.kind, action.reason), (r.Kind.HANDOFF, "submission_uncertain"))
            self.execute()
        self.assertEqual(self.parent.calls.count("begin_submit"), 1)
        self.assertNotIn("adapter", self.parent.calls)

    def test_crash_after_action_before_verification_uses_saved_observation(self):
        self.parent.put(job("one"))
        def crash(action, observation, now):
            raise SystemExit()
        hooks = self.parent.hooks()
        hooks = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard, hooks.begin_submit, crash, hooks.failure)
        with self.assertRaises(SystemExit):
            self.execute(self.runner(hooks=hooks))
        self.assertIsNone(self.state()["checkpoint"])
        action = self.runner().next_action(NOW)
        self.assertEqual(action.kind, r.Kind.REVIEW)
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_unverified_browser_action_cannot_replay_and_no_adapter_is_manual(self):
        self.parent.put(job("one"))
        self.parent.disposition = "unresolved"
        self.assertEqual(self.execute()[0].kind, r.Kind.HANDOFF)
        self.assertEqual(self.execute()[0].reason, "unverified_action")
        self.assertEqual(self.parent.calls.count("adapter"), 1)
        self.assertIsNone(self.state()["checkpoint"])
        self.parent.disposition = "not_executed"
        action = self.runner(adapter=None).next_action(NOW)
        self.assertEqual(action.reason, "adapter_missing")
        self.assertIsNone(self.state()["pending"])
        self.assertIsNone(self.state()["checkpoint"])

    def test_alias_confirmation_empty_intent_and_transitive_dedup(self):
        self.parent.put(job("old", submissions=[confirmation(batch_id="earlier")], employer_requisition_id="req"))
        self.parent.put(job("bridge", employer_requisition_id="req", listing_url="https://example.invalid/bridge"))
        self.parent.put(job("stale", listing_url="https://example.invalid/bridge", resumed_from_answers=True))
        self.parent.put(job("pending", submission_intent={}))
        self.parent.put(job("alias", listing_url="https://example.invalid/jobs/pending"))
        self.parent.put(job("fresh"))
        self.assertEqual(self.runner().next_action(NOW).application_id, "fresh")
        self.assertEqual(self.state()["decisions"]["stale"], "confirmed")
        self.assertEqual(self.state()["decisions"]["alias"], "reconciliation_required")
        # Two fresh aliases earn only one choice, with stable lexical ordering.
        self.parent.put(job("a-copy", listing_url="https://example.invalid/jobs/fresh"))
        self.assertEqual(self.runner().next_action(NOW).application_id, "a-copy")

    def test_current_and_frozen_caps_and_deadlines(self):
        self.parent.put(job("one"))
        for field in ("daily_cap", "per_run_cap", "per_run_minutes"):
            with self.subTest(field=field):
                w.atomic_json(self.root / "settings.json", {field: 0})
                action = self.runner().next_action(NOW)
                self.assertIn(action.kind, {r.Kind.COMPLETE, r.Kind.HANDOFF})
        w.atomic_json(self.root / "settings.json", {"per_run_cap": 1})
        settings.run_start(self.root, "capped", NOW)
        w.atomic_json(self.root / "settings.json", {"per_run_cap": 10})
        self.parent.put(job("done", submissions=[confirmation(run_id="capped")]))
        runner = r.Runner(self.root, "capped", hooks=self.parent.hooks(), adapter=self.parent.adapter)
        self.assertEqual(runner.next_action(NOW).kind, r.Kind.COMPLETE)
        w.atomic_json(self.root / "settings.json", {"per_run_minutes": 1})
        settings.run_start(self.root, "deadline", NOW)
        w.atomic_json(self.root / "settings.json", {"per_run_minutes": None})
        runner = r.Runner(self.root, "deadline", hooks=self.parent.hooks(), adapter=self.parent.adapter)
        self.assertEqual(runner.next_action(NOW + timedelta(minutes=1)).reason, "deadline_reached")

    def test_unique_quota_uses_original_batch_and_categories(self):
        self.parent.target = 2
        self.parent.categories = {"job": 1, "internship": 1}
        self.parent.put(job("done", submissions=[confirmation(), confirmation()]))
        self.parent.put(job("duplicate", listing_url="https://example.invalid/jobs/done", submissions=[confirmation()]))
        self.parent.put(job("old", submissions=[confirmation(batch_id="earlier")]))
        self.parent.put(job("extra-job", category="job", resumed_from_answers=True))
        self.parent.put(job("intern", category="internship"))
        action = self.runner().next_action(NOW)
        self.assertEqual((action.application_id, action.remaining), ("intern", 1))

    def test_adapter_context_contains_no_profile_listing_or_answers(self):
        self.parent.put(job("minimal", full_profile={"secret": "synthetic-value"}, job_description="Synthetic prose",
                            answers=[{"answer": "Synthetic sensitive answer"}]))
        received = []
        def adapter(action):
            received.append(action.as_dict())
            return r.Observation(self.parent.ref)
        self.execute(self.runner(adapter=adapter))
        self.assertEqual(set(received[0]), {"kind", "run_id", "sequence", "application_id", "record_ref", "remaining", "reason"})
        serialized = json.dumps(received) + json.dumps(self.state())
        self.assertNotIn("synthetic-value", serialized)
        self.assertNotIn("Synthetic prose", serialized)
        self.assertNotIn("sensitive answer", serialized)

    def test_failure_persists_pending_and_static_reason_without_provider_text(self):
        self.parent.put(job("one"))
        def broken(action):
            raise RuntimeError("Synthetic private provider details")
        self.assertEqual(self.execute(self.runner(adapter=broken))[0].reason, "unsupported")
        self.assertIsNotNone(self.state()["pending"])
        self.assertEqual(self.state()["last_result"], "unsupported")
        self.assertEqual(self.parent.get("one")["blocker"]["reason"], "unsupported")
        self.assertNotIn("private provider", json.dumps(self.state()))
        self.assertEqual(self.runner().next_action(NOW).reason, "unverified_action")

    def test_submission_failure_persists_uncertainty_and_no_checkpoint(self):
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        def timeout(action):
            raise TimeoutError()
        self.assertEqual(self.execute(self.runner(adapter=timeout))[0].reason, "submission_uncertain")
        self.assertEqual(self.parent.get("one")["blocker"]["reason"], "submission_uncertain")
        self.assertIsNone(self.state()["checkpoint"])
        self.assertEqual(self.runner().next_action(NOW).reason, "submission_uncertain")

    def test_pending_persistence_failure_prevents_adapter(self):
        self.parent.put(job("one"))
        save = w.atomic_json
        def fail_pending(path, data):
            if path.name == "state.json" and data["pending"] is not None:
                raise OSError("Synthetic disk failure")
            return save(path, data)
        with patch.object(w, "atomic_json", side_effect=fail_pending):
            with self.assertRaises(OSError):
                self.execute()
        self.assertNotIn("adapter", self.parent.calls)
        self.assertIsNone(self.state()["pending"])

    def test_checkpoint_write_failure_retains_pending(self):
        self.parent.put(job("one"))
        save = w.atomic_json
        def fail_checkpoint(path, data):
            if path.name == "state.json" and data["checkpoint"] is not None:
                raise OSError("Synthetic checkpoint failure")
            return save(path, data)
        with patch.object(w, "atomic_json", side_effect=fail_checkpoint):
            self.assertEqual(self.execute()[0].kind, r.Kind.HANDOFF)
        self.assertIsNone(self.state()["checkpoint"])
        self.assertIsNotNone(self.state()["pending"])

    def test_gate_refusal_begin_refusal_missing_callbacks_and_step_bound(self):
        self.parent.put(job("one"))
        hooks = self.parent.hooks()
        def refuse(action, now):
            raise ValueError("Synthetic gate refusal")
        refused = r.Hooks(hooks.writer, hooks.recover, hooks.queue, refuse, hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.execute(self.runner(hooks=refused))[0].reason, "gate_blocked")
        self.assertIsNone(self.state()["pending"])
        self.assertNotIn("adapter", self.parent.calls)
        self.assertEqual(len(self.execute(steps=2)), 2)
        self.assertEqual(self.parent.calls.count("adapter"), 2)
        self.parent.authorize = False
        self.assertEqual(self.execute()[0].reason, "submission_uncertain")
        self.assertEqual(self.parent.calls.count("adapter"), 2)
        runner = r.Runner(self.root, "run-test")
        self.assertEqual(runner.next_action(NOW).reason, "callbacks_missing")
        self.assertEqual(self.execute(runner, steps=0), [])
        with self.assertRaises(ValueError):
            self.execute(steps=101)

    def test_no_adapter_immutable_run_and_corrupt_or_escaping_state(self):
        self.parent.put(job("one"))
        run_path = self.root / "applications/runs/run-test/run.json"
        original = run_path.read_bytes()
        self.assertEqual(self.execute(self.runner(adapter=None))[0].reason, "adapter_missing")
        self.assertIsNone(self.state()["checkpoint"])
        self.assertEqual(run_path.read_bytes(), original)
        state_path = self.root / "applications/runs/run-test/state.json"
        state_path.write_text('{"schema_version": 99}')
        with self.assertRaises(ValueError):
            self.runner().next_action(NOW)
        state_path.unlink()
        with tempfile.TemporaryDirectory() as outside:
            state_path.symlink_to(Path(outside) / "state.json")
            with self.assertRaises(ValueError):
                self.runner().next_action(NOW)

    def test_late_confirmation_checkpoint_recovery_after_cap_tightens(self):
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        hooks = self.parent.hooks()
        def exit_after_confirmation(action, observation, now):
            hooks.verify(action, observation, now)
            raise SystemExit()
        interrupted = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard,
                              hooks.begin_submit, exit_after_confirmation, hooks.failure)
        with self.assertRaises(SystemExit):
            self.execute(self.runner(hooks=interrupted))
        self.assertIsNotNone(self.state()["pending"])
        w.atomic_json(self.root / "settings.json", {"per_run_cap": 0})
        self.assertEqual(self.runner().next_action(NOW).kind, r.Kind.COMPLETE)
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.state()["checkpoint"]["action"]["kind"], "submit")
        self.assertEqual(self.parent.calls.count("begin_submit"), 1)
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_failure_callback_commit_then_export_error_is_not_double_counted(self):
        self.parent.put(job("one"))
        hooks = self.parent.hooks()
        def fail_export(action, reason, now):
            hooks.failure(action, reason, now)
            raise OSError("Synthetic export interruption")
        interrupted = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard,
                              hooks.begin_submit, hooks.verify, fail_export)
        def timeout(action):
            return r.Observation(failure=retry_policy.Reason.TIMEOUT)
        result = self.execute(self.runner(hooks=interrupted, adapter=timeout))[0]
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(self.parent.get("one")["retry_counts"], {"timeout": 1})
        self.assertEqual(self.parent.calls.count("failure:timeout"), 1)
        self.assertIsNotNone(self.state()["pending"])
        self.assertEqual(self.runner().next_action(NOW).reason, "unverified_action")

    def test_proven_non_execution_does_not_advance_checkpoint_or_claim_success(self):
        self.parent.put(job("one"))
        self.parent.put(job("two"))
        self.parent.disposition = "not_executed"
        def timeout(action):
            return r.Observation(self.parent.ref, retry_policy.Reason.TIMEOUT)
        result = self.execute(self.runner(adapter=timeout), steps=3)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].kind, result[0].reason), (r.Kind.HANDOFF, "not_executed"))
        self.assertIsNone(self.state()["checkpoint"])
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.runner().next_action(NOW).application_id, "two")

    def test_submit_cannot_verify_without_existing_confirmation_flow(self):
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        hooks = self.parent.hooks()
        def unproven(action, observation, now):
            return r.Verification("verified", self.parent.ref)
        invalid = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard,
                          hooks.begin_submit, unproven, hooks.failure)
        self.assertEqual(self.execute(self.runner(hooks=invalid))[0].reason, "submission_uncertain")
        self.assertIsNone(self.state()["checkpoint"])
        self.assertIsNotNone(self.state()["pending"])

    def test_saved_batch_target_cannot_be_relaxed_on_restart(self):
        self.parent.target = 1
        self.parent.put(job("one"))
        self.assertEqual(self.runner().next_action(NOW).remaining, 1)
        self.parent.target = 10
        self.parent.put(job("done", submissions=[confirmation()]))
        self.assertEqual(self.runner().next_action(NOW).kind, r.Kind.COMPLETE)
        self.assertEqual(self.state()["batch"]["target"], 1)

    def test_plan_is_rechecked_at_execution_and_false_guard_never_authorizes(self):
        self.parent.put(job("one"))
        self.assertEqual(self.runner().next_action(NOW).kind, r.Kind.PREPARE)
        w.atomic_json(self.root / "settings.json", {"daily_cap": 0})
        self.assertEqual(self.execute()[0].kind, r.Kind.COMPLETE)
        self.assertNotIn("adapter", self.parent.calls)
        w.atomic_json(self.root / "settings.json", {})
        hooks = self.parent.hooks()
        false_guard = r.Hooks(hooks.writer, hooks.recover, hooks.queue, lambda *_: False,
                              hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.execute(self.runner(hooks=false_guard))[0].reason, "gate_blocked")
        self.assertNotIn("adapter", self.parent.calls)

    def test_competing_executors_share_one_writer_and_reload_verified_stage(self):
        from concurrent.futures import ThreadPoolExecutor

        self.parent.put(job("one"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.execute()[0].kind, range(2)))
        self.assertEqual(set(results), {r.Kind.PREPARE, r.Kind.REVIEW})
        self.assertEqual(self.state()["sequence"], 2)
        self.assertEqual(self.parent.calls.count("adapter"), 2)

class WorkflowExportRegressionTests(unittest.TestCase):
    def test_workflow_includes_checkpoint_on_success_stop_and_adapter_failure(self):
        from dataclasses import replace
        import spans
        for mode in ('success', 'stop', 'failure', 'export_failure'):
            with self.subTest(mode=mode):
                # Each case gets a separate run ledger and authoritative state.
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    w.atomic_json(root/'settings.json', {})
                    settings.run_start(root, 'run-test', NOW)
                    parent = Parent(root)
                    parent.put(job('one'))
                    seconds = [0]
                    checkpoints = []
                    def adapter(action):
                        seconds[0] += 1
                        if mode == 'failure': raise TimeoutError()
                        return parent.adapter(action)
                    def guard(action, now):
                        parent.guard(action, now)
                        if mode == 'stop':
                            seconds[0] += 1
                            raise ValueError('stopped')
                    def checkpoint():
                        checkpoints.append(1)
                        seconds[0] += 5
                        if mode == 'export_failure': raise OSError('synthetic export failure')
                        return {'ok':True}
                    controller = r.Runner(root, 'run-test', adapter=adapter,
                        hooks=replace(parent.hooks(), guard=guard, checkpoint=checkpoint),
                        monotonic=lambda:seconds[0])
                    result = controller.execute(clock=lambda:NOW+timedelta(seconds=seconds[0]))
                    summary = spans.summarize([],spans.load(root))
                    self.assertEqual(summary['wall_run_seconds'],6)
                    self.assertEqual(summary['critical_path_active_seconds'],6)
                    self.assertEqual(checkpoints,[1])
                    self.assertEqual(result[0].kind, r.Kind.PREPARE if mode in {'success','export_failure'} else r.Kind.HANDOFF)
                    self.assertEqual(parent.calls.count('adapter'),1 if mode in {'success','export_failure'} else 0)
                    self.assertEqual(controller.export_outcome['ok'],mode != 'export_failure')
                    if mode == 'export_failure':
                        self.assertIsNone(w.read(root/'applications/runs/run-test/state.json')['pending'])

    def test_checkpoint_failure_cannot_replace_confirmation_or_replay_submit(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            w.atomic_json(root/'settings.json', {})
            settings.run_start(root, 'run-test', NOW)
            parent = Parent(root)
            parent.target = 1
            parent.put(job('one', status='ready', prepared={'review_snapshot':parent.ref}))
            def checkpoint(): raise OSError('synthetic derived export failure')
            controller = r.Runner(root, 'run-test', hooks=replace(parent.hooks(), checkpoint=checkpoint), adapter=parent.adapter)
            self.assertEqual(controller.execute(clock=lambda:NOW)[0].kind, r.Kind.SUBMIT)
            self.assertEqual(controller.export_outcome, {'ok':False, 'status':'failed'})
            self.assertEqual(parent.get('one')['status'], 'submitted')
            self.assertEqual(len(parent.get('one')['submissions']), 1)
            self.assertIsNone(w.read(root/'applications/runs/run-test/state.json')['pending'])
            self.assertEqual(controller.execute(clock=lambda:NOW)[0].kind, r.Kind.COMPLETE)
            self.assertEqual(parent.calls.count('adapter'), 1)

    def test_unexpected_workflow_failure_still_checkpoints_and_preserves_exception(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            w.atomic_json(root/'settings.json', {})
            settings.run_start(root, 'run-test', NOW)
            parent = Parent(root)
            calls = []
            def queue(*args): raise ValueError('synthetic queue failure')
            def checkpoint():
                calls.append(1)
                raise OSError('synthetic derived failure')
            controller = r.Runner(root, 'run-test', hooks=replace(parent.hooks(), queue=queue, checkpoint=checkpoint), adapter=parent.adapter)
            with self.assertRaisesRegex(ValueError, 'synthetic queue failure'):
                controller.execute(clock=lambda:NOW)
            self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
