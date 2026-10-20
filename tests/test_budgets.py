"""Synthetic budget boundaries, restarts and interruption; no live adapters."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import budgets
import retry_policy
import runner as r
import settings
import workspace as w
from test_runner import Parent, NOW, job, confirmation


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = Parent(self.root)
        self.clock = Clock()

    def start(self, **config):
        self.config(**config)
        settings.run_start(self.root, "run-test", NOW)
        self.original = (self.root / "applications/runs/run-test/run.json").read_bytes()
        self.parent.put(job("one"))

    def config(self, **values):
        w.atomic_json(self.root / "settings.json", {"timezone": "Asia/Singapore", **values})

    def controller(self, adapter=None, hooks=None):
        return r.Runner(self.root, "run-test", hooks=hooks or self.parent.hooks(),
                        adapter=adapter or self.parent.adapter)

    def run_steps(self, steps=1, **kwargs):
        return self.controller(**kwargs).execute(clock=self.clock, max_steps=steps)

    def ledger(self):
        return w.read(self.root / budgets.reference("run-test"))

    def state(self):
        return w.read(self.root / "applications/runs/run-test/state.json")

    def test_strict_optional_policy_and_usage(self):
        self.assertEqual(settings.validate_settings({})["work_budget"], budgets.DEFAULT_POLICY)
        self.assertEqual(settings.validate_settings({"work_budget": {"max_actions": 0}})["work_budget"]["max_retries"], 3)
        for raw in (None, [], {"extra": 1}, {"max_actions": None}, {"max_retries": True},
                    {"max_actions": -1}, {"max_retries": 1.5}, {"provider_token_cap": 1.5},
                    {"provider_cost_cap": False}, {"provider_cost_cap": float("nan")},
                    {"provider_cost_cap": float("inf")}):
            with self.subTest(raw=raw), self.assertRaises(settings.SettingsError):
                settings.validate_settings({"work_budget": raw})
        for usage in ([], {"estimated_cost": 1}, {"tokens": True}, {"tokens": 1.5},
                      {"cost": -1}, {"cost": "1"}, {"cost": float("nan")}, {"cost": float("inf")}):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                r.Observation(usage=usage)
        self.assertIsNone(r.Observation().usage)
        self.assertEqual(r.Observation(usage={"tokens": 0}).usage, {"tokens": 0, "cost": None})

    def test_legacy_raw_frozen_hash_and_bytes_remain_valid(self):
        self.start()
        path = self.root / "applications/runs/run-test/run.json"
        saved = w.read(path)
        saved["settings"] = {k: v for k, v in saved["settings"].items() if k in settings.FROZEN_V1_REQUIRED}
        saved["settings_hash"] = hashlib.sha256(json.dumps(saved["settings"], sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        w.atomic_json(path, saved)
        original = path.read_bytes()
        self.assertEqual(settings.load_run(self.root, "run-test"), saved)
        self.run_steps()
        self.assertEqual(path.read_bytes(), original)

    def test_action_reservation_precedes_adapter_and_survives_restart(self):
        self.start(work_budget={"max_actions": 2})
        def adapter(action):
            self.assertEqual(self.ledger()["actions"][-1], {"sequence": action.sequence, "usage": None})
            self.assertEqual(self.state()["pending"]["action"]["sequence"], action.sequence)
            return self.parent.adapter(action)
        self.assertEqual(self.run_steps(adapter=adapter)[0].kind, r.Kind.PREPARE)
        results = self.run_steps(steps=5, adapter=adapter)
        self.assertEqual([a.kind for a in results], [r.Kind.REVIEW, r.Kind.HANDOFF])
        self.assertEqual(results[-1].reason, "action_budget_reached")
        self.assertEqual(results[-1].as_dict()["budget_ref"], budgets.reference("run-test"))
        self.assertEqual(len(self.ledger()["actions"]), 2)
        self.assertEqual(self.state()["checkpoint"]["action"]["kind"], "review")
        self.assertIsNone(self.state()["pending"])
        self.assertEqual((self.root / "applications/runs/run-test/run.json").read_bytes(), self.original)

    def test_frozen_and_current_minimum_and_zero_action_limit(self):
        self.start(work_budget={"max_actions": 1})
        self.config(work_budget={"max_actions": 9})
        self.run_steps()
        self.assertEqual(self.run_steps()[0].reason, "action_budget_reached")
        self.config(work_budget={"max_actions": 0})
        self.assertEqual(self.run_steps()[0].reason, "action_budget_reached")
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_budget_write_failure_prevents_adapter_and_keeps_pending(self):
        self.start()
        self.controller().plan_next(NOW)  # Create the empty ledger.
        atomic = w.atomic_json
        def fail_reservation(path, data):
            if path.name == "budget.json" and data["actions"]:
                raise OSError("Synthetic reservation failure")
            return atomic(path, data)
        with patch.object(w, "atomic_json", side_effect=fail_reservation), self.assertRaises(OSError):
            self.run_steps()
        self.assertNotIn("adapter", self.parent.calls)
        self.assertIsNotNone(self.state()["pending"])

    def test_current_action_limit_tightened_during_guard_stops_call(self):
        self.start(work_budget={"max_actions": 5})
        hooks = self.parent.hooks()
        def guard(action, now):
            hooks.guard(action, now)
            self.config(work_budget={"max_actions": 0})
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, guard, hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.run_steps(hooks=changed)[0].reason, "action_budget_reached")
        self.assertEqual(self.ledger()["actions"], [])
        self.assertNotIn("adapter", self.parent.calls)

    def test_failed_action_consumes_budget_even_after_proven_non_execution(self):
        self.start(work_budget={"max_actions": 1})
        def broken(action):
            raise TimeoutError("Synthetic private error")
        self.assertEqual(self.run_steps(adapter=broken)[0].reason, "unsupported")
        self.assertEqual(len(self.ledger()["actions"]), 1)
        self.parent.disposition = "not_executed"
        self.assertEqual(self.run_steps()[0].reason, "action_budget_reached")
        self.assertIsNone(self.state()["checkpoint"])
        self.assertIsNone(self.state()["pending"])
        self.assertNotIn("private error", json.dumps(self.ledger()))

    def test_deadline_crossed_during_guard_stops_before_pending_or_call(self):
        self.start(per_run_minutes=1)
        hooks = self.parent.hooks()
        def guard(action, now):
            hooks.guard(action, now)
            self.clock.advance(minutes=1)
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, guard, hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.run_steps(hooks=changed)[0].reason, "deadline_reached")
        self.assertEqual(self.run_steps()[0].reason, "deadline_reached")
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.ledger()["actions"], [])
        self.assertNotIn("adapter", self.parent.calls)

    def test_quota_rechecked_after_guard(self):
        self.start()
        self.parent.target = 1
        hooks = self.parent.hooks()
        def guard(action, now):
            hooks.guard(action, now)
            self.parent.put(job("other", submissions=[confirmation()]))
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, guard, hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.run_steps(hooks=changed)[0].reason, "quota_reached")
        self.assertNotIn("adapter", self.parent.calls)
        self.assertEqual(self.ledger()["actions"], [])

    def test_retry_limit_current_frozen_saved_minimum_and_exhausted_record(self):
        self.start(work_budget={"max_retries": 1})
        self.config(work_budget={"max_retries": 9})
        saved = retry_policy.next_retry_state(self.parent.get("one"), "timeout", NOW, max_retries=1)
        later = NOW + timedelta(minutes=2)
        saved = retry_policy.next_retry_state(saved, "timeout", later, max_retries=9)
        self.parent.put(saved)
        self.clock.advance(minutes=10)
        self.assertEqual(budgets.retry_limit(self.root, saved), 1)
        self.assertNotEqual(self.controller().plan_next(self.clock()).application_id, "one")
        self.assertEqual(self.state()["decisions"]["one"], "exhausted")
        self.config(work_budget={"max_retries": 0})
        self.assertEqual(budgets.retry_limit(self.root, saved), 0)
        saved["retry_budget"] = 0
        self.config(work_budget={"max_retries": 9})
        self.assertEqual(budgets.retry_limit(self.root, saved), 0)

    def test_retry_exhaustion_rechecked_after_guard(self):
        self.start(work_budget={"max_retries": 0})
        hooks = self.parent.hooks()
        def guard(action, now):
            hooks.guard(action, now)
            self.parent.put(retry_policy.next_retry_state(self.parent.get("one"), "timeout", now, max_retries=0))
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, guard, hooks.begin_submit, hooks.verify, hooks.failure)
        self.assertEqual(self.run_steps(hooks=changed)[0].reason, "retry_exhausted")
        self.assertEqual(self.ledger()["actions"], [])

    def test_unknown_usage_with_cap_hands_off_without_estimates(self):
        self.start(work_budget={"provider_token_cap": 50})
        self.assertEqual(self.run_steps()[0].kind, r.Kind.PREPARE)
        result = self.run_steps()[0]
        self.assertEqual(result.reason, "provider_usage_unavailable")
        self.assertEqual(result.budget_ref, budgets.reference("run-test"))
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": None, "cost": None})
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_unknown_usage_without_cap_still_obeys_deadline(self):
        self.start(per_run_minutes=1)
        self.run_steps()
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": None, "cost": None})
        self.clock.advance(minutes=1)
        self.assertEqual(self.run_steps()[0].reason, "deadline_reached")
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_only_measured_deltas_reach_provider_cap_across_restart(self):
        self.start(work_budget={"provider_token_cap": 4, "provider_cost_cap": 1})
        def adapter(action):
            self.parent.adapter(action)
            return r.Observation(usage={"tokens": 2, "cost": 0.4})
        self.run_steps(adapter=adapter)
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": 2, "cost": 0.4})
        self.run_steps(adapter=adapter)
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": 4, "cost": 0.8})
        self.assertEqual(self.run_steps(adapter=adapter)[0].reason, "provider_budget_reached")
        self.assertEqual(self.parent.calls.count("adapter"), 2)

    def test_partial_usage_is_unknown_only_for_omitted_metric_and_zero_is_measured(self):
        self.start(work_budget={"provider_token_cap": 10})
        def adapter(action):
            self.parent.adapter(action)
            return r.Observation(usage={"tokens": 0})
        self.run_steps(adapter=adapter)
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": 0, "cost": None})
        self.assertEqual(self.controller().plan_next(NOW).kind, r.Kind.REVIEW)
        self.config(work_budget={"provider_cost_cap": 1})
        self.assertEqual(self.run_steps()[0].reason, "provider_usage_unavailable")

    def test_frozen_measured_cost_cap_cannot_be_relaxed(self):
        self.start(work_budget={"provider_cost_cap": 0.5})
        self.config(work_budget={"provider_cost_cap": None})
        def adapter(action):
            self.parent.adapter(action)
            return r.Observation(usage={"cost": 0.5})
        self.run_steps(adapter=adapter)
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": None, "cost": 0.5})
        self.assertEqual(self.run_steps()[0].reason, "provider_budget_reached")
        self.assertEqual(self.parent.calls.count("adapter"), 1)

    def test_measured_zero_cap_blocks_first_call(self):
        self.start(work_budget={"provider_cost_cap": 0})
        self.assertEqual(self.run_steps()[0].reason, "provider_budget_reached")
        self.assertNotIn("adapter", self.parent.calls)
        self.assertIsNone(self.ledger()["provider_usage"]["cost"])

    def test_unknown_history_cannot_be_replaced_by_later_measured_usage(self):
        self.start()
        self.run_steps()
        def adapter(action):
            self.parent.adapter(action)
            return r.Observation(usage={"tokens": 5, "cost": 0.1})
        self.run_steps(adapter=adapter)
        self.assertEqual(self.ledger()["provider_usage"], {"tokens": None, "cost": None})
        self.config(work_budget={"provider_token_cap": 100})
        self.assertEqual(self.run_steps()[0].reason, "provider_usage_unavailable")

    def test_interrupted_begun_submit_reserves_once_and_never_replays_after_deadline(self):
        self.start(per_run_minutes=1, work_budget={"max_actions": 1})
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        def interrupted(action):
            self.assertIsNotNone(self.parent.get("one")["submission_intent"])
            self.assertEqual(len(self.ledger()["actions"]), 1)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_steps(adapter=interrupted)
        self.clock.advance(minutes=1)
        self.assertEqual(self.run_steps()[0].reason, "submission_uncertain")
        self.assertIsNotNone(self.state()["pending"])
        self.assertIsNone(self.state()["checkpoint"])
        self.assertEqual(self.parent.calls.count("begin_submit"), 1)
        self.assertEqual(len(self.ledger()["actions"]), 1)

    def test_submit_recheck_after_begin_blocks_late_click_preserving_claim(self):
        self.start(per_run_minutes=1)
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        hooks = self.parent.hooks()
        def begin(action, now):
            result = hooks.begin_submit(action, now)
            self.clock.advance(minutes=1)
            return result
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard, begin, hooks.verify, hooks.failure)
        self.assertEqual(self.run_steps(hooks=changed)[0].reason, "submission_uncertain")
        self.assertNotIn("adapter", self.parent.calls)
        self.assertIsNotNone(self.parent.get("one")["submission_intent"])
        self.assertIsNotNone(self.state()["pending"])
        self.assertEqual(len(self.ledger()["actions"]), 1)

    def test_late_confirmation_recovers_checkpoint_even_when_limits_expired(self):
        self.start(per_run_minutes=1, work_budget={"max_actions": 1})
        self.parent.put(job("one", status="ready", prepared={"review_snapshot": self.parent.ref}))
        hooks = self.parent.hooks()
        def verify_then_exit(action, observation, now):
            hooks.verify(action, observation, now)
            raise SystemExit()
        changed = r.Hooks(hooks.writer, hooks.recover, hooks.queue, hooks.guard, hooks.begin_submit, verify_then_exit, hooks.failure)
        with self.assertRaises(SystemExit):
            self.run_steps(hooks=changed)
        self.clock.advance(minutes=1)
        self.assertEqual(self.run_steps()[0].reason, "deadline_reached")
        self.assertEqual(self.state()["checkpoint"]["action"]["kind"], "submit")
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.parent.calls.count("adapter"), 1)
        self.assertEqual(self.parent.get("one")["status"], "submitted")

    def test_missing_or_corrupt_budget_cannot_reset_executed_work(self):
        self.start()
        self.run_steps()
        path = self.root / budgets.reference("run-test")
        saved = self.ledger()
        path.unlink()
        self.assertEqual(self.run_steps()[0].reason, "budget_state_missing")
        saved["actions"][0]["usage"] = {"tokens": True}
        w.atomic_json(path, saved)
        with self.assertRaises(ValueError):
            self.run_steps()
        self.assertEqual(self.parent.calls.count("adapter"), 1)
