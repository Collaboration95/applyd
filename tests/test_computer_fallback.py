"""Synthetic adapter tests only. Fixture PNGs are NOT browser captures."""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from computer_fallback import Action, Observation, Outcome, effective_policy, run_session, run_workspace_session
import settings


# Synthetic 1x1 image; every reference is explicitly labelled fixture.
FIXTURE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9r8AAAAASUVORK5CYII=")
URL = "https://jobs.example.test/application"
TAB = "fixture-tab-1"
WIDGET = Action("select_option", "fixture-language", "Python")


class FakeAdapter:
    """No OS or browser automation. Only synthetic temporary evidence files."""

    def __init__(self, root):
        self.root = root
        self.tab_id, self.url, self.easy_apply = TAB, URL, False
        self.observations = 0
        self.performed = []
        self.missing = set()
        self.on_observe = None
        self.on_perform = None
        self.record = root / "applications/runs/fixture-run/computer-use/fixture-session.json"

    def observe(self):
        self.observations += 1
        screenshot = None
        if self.observations not in self.missing:
            screenshot = self.root / f"applications/runs/fixture-run/evidence/fixture-{self.observations}.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            screenshot.write_bytes(FIXTURE_PNG)
            screenshot = screenshot.relative_to(self.root).as_posix()
        observation = Observation(self.tab_id, self.url, screenshot, self.easy_apply)
        return self.on_observe(observation) if self.on_observe else observation

    def perform(self, action, **scope):
        # The budget and pending entry must already be durable at action time.
        saved = json.loads(self.record.read_text())
        assert saved["step_count"] == len(self.performed) + 1
        assert saved["actions"][-1]["status"] == "pending"
        self.performed.append((action, scope))
        if self.on_perform:
            self.on_perform()


class ComputerFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.adapter = FakeAdapter(self.root)
        self.policy = {"mode": "apply", "easy_apply_only": False,
                       "computer_use": {"enabled": True, "step_budget": 6},
                       "external_hosts": {"allow": ["example.test"], "deny": []}}

    def run_fallback(self, **overrides):
        arguments = dict(policy=self.policy, adapter=self.adapter, initial_tab_id=TAB,
                         initial_url=URL, failure_kind="custom_widget", dom_failed=True, actions=[WIDGET],
                         guard=lambda phase: True)
        arguments.update(overrides)
        return run_session(self.root, "fixture-run", "fixture-session", **arguments)

    def record(self):
        return json.loads(self.adapter.record.read_text())

    def fresh(self):
        # Only tests create distinct sessions by starting a new temporary workspace.
        self.setUp()

    def test_opt_in_and_apply_mode_required_without_adapter_calls(self):
        for policy in ({}, {"computer_use": {"enabled": False}},
                       {**self.policy, "mode": "dry_run"}, {**self.policy, "mode": "save_only"}):
            with self.subTest(policy=policy):
                self.fresh()
                result = self.run_fallback(policy=policy)
                self.assertEqual(result.outcome, Outcome.DISABLED)
                self.assertEqual(self.adapter.observations, 0)
                self.assertEqual(self.record()["outcome"], "disabled")

    def test_requires_explicit_allow_deny_wins_and_linkedin_is_always_blocked(self):
        cases = [({}, URL), ({"allow": [], "deny": []}, URL),
                 ({"allow": ["example.test"], "deny": ["jobs.example.test"]}, URL),
                 ({"allow": ["example.test"], "deny": ["example.test"]}, URL),
                 ({"allow": ["linkedin.com"], "deny": []}, "https://www.linkedin.com/jobs/view/1"),
                 ({"allow": ["example.test"], "deny": []}, "https://example.test.attacker.invalid/"),
                 ({"allow": ["example.test"], "deny": []}, "file:///tmp/fixture.html"),
                 ({"allow": ["example.test"], "deny": []}, "https://@jobs.example.test/form"),
                 ({"allow": ["example.test"], "deny": []}, "https://" + "user:pass@jobs.example.test/form"),
                 ({"allow": ["*.test"], "deny": []}, URL),
                 ({"allow": ["https://example.test"], "deny": []}, URL)]
        for external, url in cases:
            with self.subTest(external=external, url=url):
                self.fresh()
                self.policy["external_hosts"] = external
                self.assertEqual(self.run_fallback(initial_url=url).outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.observations, 0)

    def test_success_logs_both_existing_private_fixture_refs_and_scope(self):
        result = self.run_fallback()
        self.assertEqual(result.outcome, Outcome.COMPLETED)
        self.assertEqual(result.step_count, 1)
        record = self.record()
        self.assertEqual(record["actions"][0]["action"], {
            "kind": "select_option", "target": "fixture-language", "value": "Python"})
        for phase in ("before", "after"):
            ref = record["actions"][0][phase]["screenshot"]
            self.assertIn("fixture-", ref)
            self.assertTrue((self.root / ref).is_file())
        self.assertEqual(self.adapter.performed[0][1], {
            "tab_id": TAB, "origin": "https://jobs.example.test:443", "resume_path": None})
        self.assertTrue(record["updated_at"].endswith("+08:00"))
        self.assertEqual(record["outcome"], "completed")

    def test_dom_failure_is_required_and_limited_to_named_widget_categories(self):
        for overrides in ({"dom_failed": False}, {"dom_failed": "yes"},
                          {"failure_kind": "text_input"}, {"failure_kind": ["canvas"]}):
            with self.subTest(overrides=overrides):
                self.fresh()
                self.assertEqual(self.run_fallback(**overrides).outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.observations, 0)

    def test_easy_apply_mode_and_detected_easy_apply_never_interact(self):
        self.policy["easy_apply_only"] = True
        self.assertEqual(self.run_fallback().outcome, Outcome.OFF_SCOPE)
        self.fresh()
        self.adapter.easy_apply = True
        self.assertEqual(self.run_fallback().outcome, Outcome.OFF_SCOPE)
        self.assertEqual(self.adapter.performed, [])

    def test_forbids_submit_send_navigation_arbitrary_clicks_keys_and_scripts(self):
        for kind in ("submit", "send", "navigate", "click", "press_key", "evaluate", "type_text", []):
            with self.subTest(kind=kind):
                self.fresh()
                result = self.run_fallback(actions=[Action(kind, "fixture-target", "ignored")])
                self.assertEqual(result.outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.observations, 0)

    def test_entire_plan_is_validated_before_any_action(self):
        result = self.run_fallback(actions=[WIDGET, Action("submit", "fixture-submit")])
        self.assertEqual(result.outcome, Outcome.OFF_SCOPE)
        self.assertEqual(self.adapter.performed, [])

    def test_callback_cannot_append_an_unvalidated_submit_to_the_plan(self):
        actions = [WIDGET]
        def guard(phase):
            if len(actions) == 1:
                actions.append(Action("submit", "fixture-submit"))
            return True
        result = self.run_fallback(actions=actions, guard=guard)
        self.assertEqual(result.outcome, Outcome.COMPLETED)
        self.assertEqual([action for action, _ in self.adapter.performed], [WIDGET])

    def test_canvas_can_set_widget_value(self):
        action = Action("set_widget_value", "fixture-canvas", "2")
        self.assertEqual(self.run_fallback(failure_kind="canvas", actions=[action]).outcome, Outcome.COMPLETED)

    def test_missing_nested_budget_uses_six_and_default_disabled_audit_keeps_six(self):
        self.policy["computer_use"] = {"enabled": True}
        self.assertEqual(self.run_fallback(actions=[WIDGET] * 7).step_count, 6)
        self.assertEqual(self.record()["outcome"], "budget_exhausted")
        self.fresh()
        self.assertEqual(self.run_fallback(policy={}).outcome, Outcome.DISABLED)
        self.assertEqual(self.record()["step_budget"], 6)

    def test_step_budget_is_reserved_before_actions_and_cannot_reset_on_restart(self):
        self.policy["computer_use"]["step_budget"] = 1
        first = self.run_fallback(actions=[WIDGET, WIDGET])
        self.assertEqual((first.outcome, first.step_count), (Outcome.BUDGET_EXHAUSTED, 1))
        self.policy["computer_use"]["step_budget"] = 100
        restart = self.run_fallback(actions=[WIDGET])
        self.assertEqual(restart, first)
        self.assertEqual(len(self.adapter.performed), 1)
        self.assertEqual(self.record()["step_budget"], 1)

    def test_zero_budget_does_not_call_adapter(self):
        self.policy["computer_use"]["step_budget"] = 0
        result = self.run_fallback()
        self.assertEqual(result.outcome, Outcome.BUDGET_EXHAUSTED)
        self.assertEqual(self.adapter.observations, 0)

    def test_exact_budget_can_complete_and_completed_session_is_not_replayed(self):
        self.policy["computer_use"]["step_budget"] = 1
        first = self.run_fallback()
        self.assertEqual(first.outcome, Outcome.COMPLETED)
        self.assertEqual(self.run_fallback(), first)
        self.assertEqual(len(self.adapter.performed), 1)

    def test_interrupt_preserves_spent_budget_and_never_replays(self):
        class FixtureInterruption(BaseException):
            pass

        def interrupt():
            raise FixtureInterruption

        self.adapter.on_perform = interrupt
        with self.assertRaises(FixtureInterruption):
            self.run_fallback()
        self.assertEqual(self.record()["step_count"], 1)
        self.assertIsNone(self.record()["outcome"])
        self.adapter.on_perform = None
        result = self.run_fallback()
        self.assertEqual((result.outcome, result.reason), (Outcome.FAILED, "interrupted_session"))
        self.assertEqual(result.step_count, 1)
        self.assertEqual(len(self.adapter.performed), 1)
        self.assertEqual(self.record()["actions"][0]["status"], "pending")

    def test_moved_tab_host_scheme_or_port_blocks_before_action(self):
        for change in ({"tab_id": "fixture-tab-2"}, {"url": "https://other.example.test/form"},
                       {"url": "http://jobs.example.test/form"}, {"url": "https://jobs.example.test:8443/form"}):
            with self.subTest(change=change):
                self.fresh()
                for name, value in change.items():
                    setattr(self.adapter, name, value)
                self.assertEqual(self.run_fallback().outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.performed, [])

    def test_scope_change_after_action_stops_rest_of_plan_and_records_actual_evidence(self):
        for change in ({"tab_id": "fixture-tab-2"}, {"url": "https://other.example.test/form"},
                       {"easy_apply": True}):
            with self.subTest(change=change):
                self.fresh()
                def move():
                    for name, value in change.items():
                        setattr(self.adapter, name, value)
                self.adapter.on_perform = move
                result = self.run_fallback(actions=[WIDGET, WIDGET])
                self.assertEqual(result.outcome, Outcome.OFF_SCOPE)
                self.assertEqual(len(self.adapter.performed), 1)
                self.assertIsNotNone(self.record()["actions"][0]["after"]["screenshot"])

    def test_scope_is_rechecked_before_next_action(self):
        def move_on_next_before(observation):
            return replace(observation, tab_id="fixture-tab-2") if self.adapter.observations == 3 else observation
        self.adapter.on_observe = move_on_next_before
        result = self.run_fallback(actions=[WIDGET, WIDGET])
        self.assertEqual((result.outcome, result.step_count), (Outcome.OFF_SCOPE, 1))

    def test_missing_before_evidence_prevents_action_and_missing_after_stops(self):
        for phase in (1, 2):
            with self.subTest(phase=phase):
                self.fresh()
                self.adapter.missing = {phase}
                result = self.run_fallback(actions=[WIDGET, WIDGET])
                self.assertEqual(result.outcome, Outcome.SCREENSHOT_MISSING)
                self.assertEqual(len(self.adapter.performed), phase - 1)
                self.assertEqual(result.step_count, phase - 1)
                if phase == 2:
                    self.assertIsNone(self.record()["actions"][0]["after"]["screenshot"])

    def test_missing_public_outside_run_or_empty_screenshot_is_rejected(self):
        for relative in ("missing-fixture.png", "tests/fixture.png", "applications/runs/other/fixture.png",
                         "applications/runs/fixture-run/evidence/empty-fixture.png"):
            with self.subTest(relative=relative):
                self.fresh()
                image = self.root / relative
                image.parent.mkdir(parents=True, exist_ok=True)
                if "missing" not in relative:
                    image.write_bytes(b"" if "empty" in relative else FIXTURE_PNG)
                self.adapter.on_observe = lambda observation: replace(observation, screenshot=relative)
                self.assertEqual(self.run_fallback().outcome, Outcome.SCREENSHOT_MISSING)
                self.assertEqual(self.adapter.performed, [])

    def test_duplicate_before_after_ref_is_not_claimed_as_new_evidence(self):
        first_ref = "applications/runs/fixture-run/evidence/fixture-1.png"
        self.adapter.on_observe = lambda observation: replace(observation, screenshot=first_ref)
        result = self.run_fallback()
        self.assertEqual((result.outcome, result.reason),
                         (Outcome.SCREENSHOT_MISSING, "distinct_after_screenshot_required"))

    def test_screenshot_symlink_cannot_escape_private_run(self):
        public = self.root / "public-fixture.png"
        public.write_bytes(FIXTURE_PNG)
        link = self.root / "applications/runs/fixture-run/evidence/link-fixture.png"
        link.parent.mkdir(parents=True)
        link.symlink_to(public)
        self.adapter.on_observe = lambda observation: replace(observation, screenshot=str(link))
        self.assertEqual(self.run_fallback().outcome, Outcome.SCREENSHOT_MISSING)

    def test_only_parent_resolved_resume_can_be_selected_and_is_logged_privately(self):
        resume = self.root / "resumes/fixture-resume.pdf"
        resume.parent.mkdir()
        resume.write_bytes(b"SYNTHETIC RESUME FIXTURE ONLY")
        upload = Action("upload_chosen_file", "fixture-upload")
        result = self.run_fallback(failure_kind="native_file_picker", actions=[upload], resolved_resume=resume)
        self.assertEqual(result.outcome, Outcome.COMPLETED)
        self.assertEqual(self.adapter.performed[0][1]["resume_path"], resume)
        self.assertEqual(self.record()["actions"][0]["resume_ref"], "resumes/fixture-resume.pdf")

    def test_upload_cannot_request_own_path_or_use_wrong_failure_category(self):
        for actions, kind in (([Action("upload_chosen_file", "fixture-upload", "arbitrary.pdf")], "native_file_picker"),
                              ([Action("upload_chosen_file", "fixture-upload")], "canvas"),
                              ([WIDGET], "native_file_picker"),
                              ([Action("upload_chosen_file", "fixture-upload")], "native_file_picker")):
            with self.subTest(actions=actions, kind=kind):
                self.fresh()
                self.assertEqual(self.run_fallback(actions=actions, failure_kind=kind).outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.performed, [])

    def test_missing_or_outside_resume_cannot_be_selected(self):
        for resume in ("missing-fixture.pdf", self.root.parent / "outside-fixture.pdf"):
            with self.subTest(resume=resume):
                self.fresh()
                result = self.run_fallback(failure_kind="native_file_picker",
                    actions=[Action("upload_chosen_file", "fixture-upload")], resolved_resume=resume)
                self.assertEqual(result.outcome, Outcome.OFF_SCOPE)
                self.assertEqual(self.adapter.performed, [])

    def test_live_resolver_selects_current_file_after_observation_before_upload(self):
        resume = self.root / "resumes/current-fixture.pdf"
        resume.parent.mkdir()
        resume.write_bytes(b"SYNTHETIC CURRENT RESUME")
        calls = []
        def resolve():
            calls.append((self.adapter.observations, len(self.adapter.performed)))
            return resume
        result = self.run_fallback(failure_kind="native_file_picker",
            actions=[Action("upload_chosen_file", "fixture-upload")], resume_resolver=resolve)
        self.assertEqual(result.outcome, Outcome.COMPLETED)
        self.assertEqual(calls, [(1, 0)])
        self.assertEqual(self.adapter.performed[0][1]["resume_path"], resume)
        self.assertEqual(self.record()["actions"][0]["resume_ref"], "resumes/current-fixture.pdf")

    def test_live_resolver_failure_cannot_fall_back_to_an_older_file(self):
        old = self.root / "old-fixture.pdf"
        old.write_bytes(b"SYNTHETIC OLD RESUME")
        def resolve():
            raise ValueError("SYNTHETIC expired current release")
        result = self.run_fallback(failure_kind="native_file_picker", resolved_resume=old,
            actions=[Action("upload_chosen_file", "fixture-upload")], resume_resolver=resolve)
        self.assertEqual(result.reason, "resume_resolution_failed")
        self.assertEqual(self.adapter.performed, [])
        self.assertNotIn("expired current release", self.adapter.record.read_text())

    def test_stop_during_resume_resolution_blocks_the_upload(self):
        resume = self.root / "fixture.pdf"
        resume.write_bytes(b"SYNTHETIC RESUME")
        stopped = []
        def resolve():
            stopped.append(True)
            return resume
        result = self.run_fallback(failure_kind="native_file_picker",
            actions=[Action("upload_chosen_file", "fixture-upload")], resume_resolver=resolve,
            guard=lambda phase: not stopped)
        self.assertEqual(result.reason, "guard_refused")
        self.assertEqual(self.adapter.performed, [])

    def test_adapter_failure_is_counted_logs_after_evidence_and_does_not_leak_exception(self):
        def fail():
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR_DO_NOT_SERIALIZE")
        self.adapter.on_perform = fail
        result = self.run_fallback(actions=[WIDGET, WIDGET])
        self.assertEqual((result.outcome, result.step_count), (Outcome.FAILED, 1))
        self.assertIsNotNone(self.record()["actions"][0]["after"]["screenshot"])
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", self.adapter.record.read_text())
        self.assertEqual(len(self.adapter.performed), 1)

    def test_unavailable_adapter_is_an_honest_typed_failure(self):
        result = self.run_fallback(adapter=None)
        self.assertEqual((result.outcome, result.reason), (Outcome.FAILED, "adapter_unavailable"))
        self.assertEqual(self.record()["step_count"], 0)

    def test_observation_errors_are_logged_without_claiming_completion(self):
        for phase in (1, 2):
            with self.subTest(phase=phase):
                self.fresh()
                def fail_observation(observation):
                    if self.adapter.observations == phase:
                        raise RuntimeError("fixture capture unavailable")
                    return observation
                self.adapter.on_observe = fail_observation
                result = self.run_fallback()
                self.assertEqual(result.outcome, Outcome.FAILED)
                self.assertEqual(result.step_count, phase - 1)

    def test_invalid_budget_or_policy_cannot_enable_actions(self):
        for computer in ({"enabled": "yes"}, {"enabled": True, "step_budget": True},
                         {"enabled": True, "step_budget": -1}, {"enabled": True, "step_budget": None},
                         {"enabled": True, "step_budget": 1.5}, []):
            with self.subTest(computer=computer):
                self.fresh()
                self.policy["computer_use"] = computer
                self.assertEqual(self.run_fallback().outcome, Outcome.FAILED)
                self.assertEqual(self.adapter.observations, 0)

    def test_corrupt_existing_state_is_not_overwritten_or_restarted(self):
        self.adapter.record.parent.mkdir(parents=True)
        self.adapter.record.write_text("{broken fixture")
        result = self.run_fallback()
        self.assertEqual(result.reason, "invalid_session_record")
        self.assertEqual(self.adapter.record.read_text(), "{broken fixture")
        self.assertEqual(self.adapter.observations, 0)

    def test_missing_outcome_or_invalid_count_does_not_reset_existing_session(self):
        for mutation in ("outcome", "step_count"):
            with self.subTest(mutation=mutation):
                self.fresh()
                self.run_fallback()
                saved = self.record()
                if mutation == "outcome":
                    del saved["outcome"]
                else:
                    saved["step_count"] = 0
                self.adapter.record.write_text(json.dumps(saved))
                result = self.run_fallback()
                self.assertEqual(result.reason, "invalid_session_record")
                self.assertEqual(len(self.adapter.performed), 1)

    def test_existing_binding_cannot_be_retargeted(self):
        first = self.run_fallback()
        original = self.adapter.record.read_bytes()
        result = self.run_fallback(initial_tab_id="fixture-tab-2")
        self.assertEqual(result.outcome, Outcome.FAILED)
        self.assertEqual(self.adapter.record.read_bytes(), original)
        self.assertEqual(len(self.adapter.performed), first.step_count)

    def test_lock_and_storage_symlinks_fail_closed(self):
        self.adapter.record.parent.mkdir(parents=True)
        lock = self.adapter.record.with_suffix(".lock")
        lock.write_text("fixture writer lock")
        self.assertEqual(self.run_fallback().reason, "session_busy")
        self.assertTrue(lock.exists())
        self.assertEqual(self.adapter.observations, 0)
        self.fresh()
        public = self.root / "public-fixture"
        public.mkdir()
        (self.root / "applications").symlink_to(public)
        with self.assertRaises(ValueError):
            self.run_fallback()
        self.assertEqual(list(public.iterdir()), [])

    def test_safe_identifiers_cannot_write_outside_run(self):
        with self.assertRaises(ValueError):
            run_session(self.root, "../public", "fixture-session", policy=self.policy,
                        adapter=self.adapter, initial_tab_id=TAB, initial_url=URL,
                        failure_kind="canvas", dom_failed=True, actions=[WIDGET])
        self.assertFalse((self.root / "applications").exists())

    def test_guard_is_required_and_refusal_never_reaches_adapter(self):
        for guard, reason in ((None, "guard_required"), (lambda phase: False, "guard_refused"),
                              (lambda phase: None, "guard_refused")):
            with self.subTest(reason=reason):
                self.fresh()
                result = self.run_fallback(guard=guard)
                self.assertEqual((result.outcome, result.reason), (Outcome.FAILED, reason))
                self.assertEqual(self.adapter.observations, 0)

    def test_guard_runs_before_every_observe_and_action_and_never_leaks_errors(self):
        phases = []
        def guard(phase):
            phases.append((phase, self.adapter.observations, len(self.adapter.performed)))
            return True
        self.assertEqual(self.run_fallback(guard=guard).outcome, Outcome.COMPLETED)
        self.assertEqual(phases, [("observe", 0, 0), ("action", 1, 0), ("observe", 1, 1)])
        for denied_call in (1, 2, 3):
            with self.subTest(denied_call=denied_call):
                self.fresh()
                calls = []
                def stop(phase):
                    calls.append(phase)
                    if len(calls) == denied_call:
                        raise RuntimeError("SYNTHETIC_PRIVATE_GUARD_ERROR")
                    return True
                result = self.run_fallback(guard=stop)
                self.assertEqual(result.reason, "guard_refused")
                self.assertEqual(self.adapter.observations, 0 if denied_call == 1 else 1)
                self.assertEqual(len(self.adapter.performed), 1 if denied_call == 3 else 0)
                self.assertNotIn("SYNTHETIC_PRIVATE_GUARD_ERROR", self.adapter.record.read_text())
                if denied_call == 3:
                    self.assertIsNone(self.record()["actions"][0]["after"])

    def test_pending_submit_claim_blocks_before_observe_and_after_guard(self):
        for create_in_guard in (False, True):
            with self.subTest(create_in_guard=create_in_guard):
                self.fresh()
                claim = self.root / "applications/browser-claim.json"
                def guard(phase):
                    claim.write_text("synthetic pending submit")
                    return True
                if not create_in_guard:
                    claim.parent.mkdir()
                    claim.write_text("synthetic pending submit")
                result = self.run_fallback(guard=guard)
                self.assertEqual(result.reason, "pending_submission_claim")
                self.assertEqual(self.adapter.observations, 0)
                self.assertEqual(claim.read_text(), "synthetic pending submit")

    def test_different_sessions_and_runs_cannot_share_browser_writer(self):
        nested = []
        def guard(phase):
            if not nested:
                nested.append(run_session(self.root, "other-run", "other-session", policy=self.policy,
                    adapter=self.adapter, initial_tab_id=TAB, initial_url=URL,
                    failure_kind="custom_widget", dom_failed=True, actions=[WIDGET], guard=lambda p: True))
            return True
        result = self.run_fallback(guard=guard)
        self.assertEqual(result.outcome, Outcome.COMPLETED)
        self.assertEqual(nested[0].reason, "browser_busy")
        self.assertEqual(len(self.adapter.performed), 1)
        self.assertFalse((self.root / "applications/computer-use.lock").exists())

    def test_abandoned_global_writer_lock_is_not_removed_or_bypassed(self):
        lock = self.root / "applications/computer-use.lock"
        lock.parent.mkdir()
        lock.write_text("synthetic interrupted writer")
        self.assertEqual(self.run_fallback().reason, "browser_busy")
        self.assertEqual(lock.read_text(), "synthetic interrupted writer")
        self.assertEqual(self.adapter.observations, 0)

    def test_effective_policy_intersects_enabled_budget_and_exact_origin(self):
        current, frozen = deepcopy(self.policy), deepcopy(self.policy)
        frozen["computer_use"]["step_budget"] = 2
        frozen["external_hosts"]["allow"] = ["jobs.example.test"]
        original = deepcopy((current, frozen))
        policy = effective_policy(current, frozen, URL)
        self.assertEqual(policy["computer_use"], {"enabled": True, "step_budget": 2})
        self.assertEqual(policy["external_hosts"]["allow"], ["jobs.example.test"])
        self.assertEqual((current, frozen), original)
        result = self.run_fallback(policy=policy, initial_url="https://jobs.example.test:8443/form")
        self.assertEqual(result.outcome, Outcome.OFF_SCOPE)
        self.assertEqual(self.adapter.observations, 0)

    def test_effective_host_allowlists_are_never_unioned_and_both_denials_win(self):
        for side in ("current", "frozen"):
            for external in ({"allow": [], "deny": []},
                             {"allow": ["other.example.test"], "deny": []},
                             {"allow": ["example.test"], "deny": ["JOBS.EXAMPLE.TEST"]}):
                with self.subTest(side=side, external=external):
                    policies = {"current": deepcopy(self.policy), "frozen": deepcopy(self.policy)}
                    policies[side]["external_hosts"] = external
                    policy = effective_policy(policies["current"], policies["frozen"], URL)
                    self.assertEqual(policy["external_hosts"]["allow"], [])

    def test_effective_policy_legacy_disabled_and_linkedin_easy_only_forbidden(self):
        for side in (0, 1):
            for change in ({"computer_use": {}}, {"easy_apply_only": True}, {"mode": "dry_run"}):
                with self.subTest(side=side, change=change):
                    self.fresh()
                    policies = [deepcopy(self.policy), deepcopy(self.policy)]
                    policies[side].update(change)
                    policy = effective_policy(*policies, URL)
                    self.assertNotEqual(self.run_fallback(policy=policy).outcome, Outcome.COMPLETED)
                    self.assertEqual(self.adapter.observations, 0)
        policy = {**self.policy, "external_hosts": {"allow": ["linkedin.com"], "deny": []}}
        self.assertEqual(effective_policy(policy, policy, "https://www.linkedin.com/jobs")["external_hosts"]["allow"], [])

    def start_workspace(self, **overrides):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.config = {**deepcopy(self.policy), **overrides}
        self.save_config()
        return settings.run_start(self.root, "fixture-run", self.now)

    def save_config(self):
        (self.root / "settings.json").write_text(json.dumps(self.config))

    def run_workspace(self, **overrides):
        arguments = dict(adapter=self.adapter, initial_tab_id=TAB, initial_url=URL,
            failure_kind="custom_widget", dom_failed=True, actions=[WIDGET],
            guard=lambda phase: True, clock=lambda: self.now)
        arguments.update(overrides)
        return run_workspace_session(self.root, "fixture-run", "fixture-session", **arguments)

    def test_workspace_requires_existing_frozen_run_and_never_starts_it_implicitly(self):
        result = self.run_workspace(clock=lambda: datetime.now(timezone.utc))
        self.assertEqual(result.reason, "policy_refresh_refused")
        self.assertFalse((self.root / "applications/runs/fixture-run/run.json").exists())
        self.assertEqual(self.adapter.observations, 0)

    def test_workspace_run_start_freezes_settings_and_current_cannot_enable_old_run(self):
        run = self.start_workspace(computer_use={"enabled": False, "step_budget": 2})
        self.assertEqual(run["settings"]["computer_use"], {"enabled": False, "step_budget": 2})
        self.config["computer_use"] = {"enabled": True, "step_budget": 6}
        self.save_config()
        self.assertEqual(self.run_workspace().outcome, Outcome.DISABLED)
        self.assertEqual(self.adapter.observations, 0)

    def test_workspace_live_settings_and_guard_can_stop_between_observe_and_action(self):
        for change, outcome in (({"computer_use": {"enabled": False}}, Outcome.DISABLED),
                                ({"computer_use": {"enabled": True, "step_budget": 0}}, Outcome.BUDGET_EXHAUSTED),
                                ({"external_hosts": {"allow": [], "deny": []}}, Outcome.OFF_SCOPE),
                                ({"per_run_minutes": 0}, Outcome.FAILED)):
            with self.subTest(change=change):
                self.fresh()
                self.start_workspace()
                def tighten(observation):
                    self.config.update(change)
                    self.save_config()
                    return observation
                self.adapter.on_observe = tighten
                result = self.run_workspace()
                self.assertEqual(result.outcome, outcome)
                self.assertEqual(self.adapter.performed, [])
                self.assertEqual(self.adapter.observations, 1)

    def test_workspace_smaller_frozen_budget_cannot_be_relaxed_by_current_settings(self):
        self.start_workspace(computer_use={"enabled": True, "step_budget": 1})
        self.config["computer_use"]["step_budget"] = 6
        self.save_config()
        result = self.run_workspace(actions=[WIDGET, WIDGET])
        self.assertEqual((result.outcome, result.step_count), (Outcome.BUDGET_EXHAUSTED, 1))

    def test_workspace_both_deadlines_use_original_start_and_aware_clock(self):
        for frozen_minutes, current_minutes in ((1, None), (None, 1), (1, 10), (10, 1)):
            with self.subTest(frozen=frozen_minutes, current=current_minutes):
                self.fresh()
                self.start_workspace(per_run_minutes=frozen_minutes)
                self.config["per_run_minutes"] = current_minutes
                self.save_config()
                self.now += timedelta(minutes=1)
                result = self.run_workspace()
                self.assertEqual(result.reason, "policy_refresh_refused")
                self.assertEqual(self.adapter.observations, 0)
        self.fresh()
        self.start_workspace()
        self.assertEqual(self.run_workspace(clock=lambda: self.now.replace(tzinfo=None)).reason,
                         "policy_refresh_refused")

    def test_settings_schema_defaults_partial_nested_values_and_rejections(self):
        self.assertEqual(settings.load_settings(self.root)["computer_use"], {"enabled": False, "step_budget": 6})
        for raw, expected in (({}, {"enabled": False, "step_budget": 6}),
                              ({"enabled": True}, {"enabled": True, "step_budget": 6}),
                              ({"step_budget": 0}, {"enabled": False, "step_budget": 0})):
            self.assertEqual(settings.validate_settings({"computer_use": raw})["computer_use"], expected)
        for raw in (None, [], True, {"enabled": 1}, {"enabled": None}, {"step_budget": True},
                    {"step_budget": -1}, {"step_budget": None}, {"step_budget": 1.5},
                    {"step_budget": "6"}, {"enabled": True, "extra": False}):
            with self.subTest(raw=raw), self.assertRaises(settings.SettingsError):
                settings.validate_settings({"computer_use": raw})
        (self.root / "settings.json").write_text('{"computer_use":{"enabled":true,"enabled":false}}')
        with self.assertRaises(settings.SettingsError):
            settings.load_settings(self.root)

    def test_original_v1_hash_bytes_stay_valid_and_optional_fallback_is_disabled(self):
        run = self.start_workspace()
        run["settings"] = {key: run["settings"][key] for key in settings.FROZEN_V1_REQUIRED}
        run["settings_hash"] = hashlib.sha256(json.dumps(run["settings"], sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        path = self.root / "applications/runs/fixture-run/run.json"
        path.write_text(json.dumps(run))
        original = path.read_bytes()
        self.assertEqual(settings.load_run(self.root, "fixture-run"), run)
        self.assertEqual(self.run_workspace().outcome, Outcome.DISABLED)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn("computer_use", settings.FROZEN_V1_REQUIRED)


if __name__ == "__main__":
    unittest.main()
