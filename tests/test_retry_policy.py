"""Synthetic state and fake clocks only: no workspace, browser or provider I/O."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from retry_policy import (Decision, MAX_BACKOFF_SECONDS, Reason, check_retry,
                          next_retry_state, resolve_answer_blocker, resume_retry)


class RetryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 9, tzinfo=timezone(timedelta(hours=8)))
        self.record = {"status": "failed", "submissions": [], "events": []}

    def restart(self, state):
        return json.loads(json.dumps(state))

    def after_backoff(self, state):
        return datetime.fromisoformat(state["next_eligible_at"])

    def test_fresh_record_is_eligible_and_check_does_not_initialize_it(self):
        before = deepcopy(self.record)
        self.assertEqual(check_retry(self.record, self.now).decision, Decision.ELIGIBLE)
        self.assertEqual(self.record, before)

    def test_repeated_timeout_exhausts_three_retries_across_restarts(self):
        state, now = self.record, self.now
        for count in range(1, 5):
            state = self.restart(next_retry_state(state, "timeout", now))
            self.assertEqual(state["retry_counts"], {"timeout": count})
            now = self.after_backoff(state)
            expected = Decision.ELIGIBLE if count <= 3 else Decision.EXHAUSTED
            self.assertEqual(check_retry(state, now).decision, expected)
        for _ in range(10):
            self.assertEqual(next_retry_state(state, "timeout", now), state)

    def test_backoff_boundary_and_repeated_polls_do_not_extend_deadline(self):
        state = next_retry_state(self.record, "timeout", self.now)
        deadline = self.after_backoff(state)
        self.assertEqual(deadline - self.now, timedelta(seconds=60))
        for _ in range(5):
            self.assertEqual(check_retry(state, deadline - timedelta(microseconds=1)).decision, Decision.BACKOFF)
            self.assertEqual(next_retry_state(state, "timeout", self.now), state)
        self.assertEqual(check_retry(state, deadline).decision, Decision.ELIGIBLE)
        second = next_retry_state(state, "timeout", deadline)
        self.assertEqual(self.after_backoff(second) - deadline, timedelta(seconds=120))
        self.assertTrue(second["next_eligible_at"].endswith("+08:00"))

    def test_alternating_reasons_share_the_same_finite_budget(self):
        state = next_retry_state(self.record, "timeout", self.now, max_retries=1)
        state = next_retry_state(state, "throttling", self.after_backoff(state), max_retries=1)
        self.assertEqual(state["retry_counts"], {"timeout": 1, "throttling": 1})
        self.assertEqual(check_retry(state, self.after_backoff(state)).decision, Decision.EXHAUSTED)

    def test_delay_is_capped_even_with_large_base_and_attempt_count(self):
        state = next_retry_state(self.record, "timeout", self.now, max_retries=30, backoff_seconds=10**20)
        for _ in range(15):
            now = self.after_backoff(state)
            state = next_retry_state(state, "throttling", now, max_retries=30, backoff_seconds=10**20)
            self.assertEqual(self.after_backoff(state) - now, timedelta(seconds=MAX_BACKOFF_SECONDS))

    def test_elapsed_backoff_across_daylight_saving_transition(self):
        now = datetime(2026, 11, 1, 1, 59, 30, tzinfo=ZoneInfo("America/New_York"))
        state = next_retry_state(self.record, "timeout", now)
        self.assertEqual(self.after_backoff(state).astimezone(timezone.utc) - now.astimezone(timezone.utc), timedelta(seconds=60))
        self.assertEqual(check_retry(state, self.after_backoff(state)).decision, Decision.ELIGIBLE)

    def test_current_policy_tightens_but_cannot_raise_frozen_budget(self):
        state = next_retry_state(self.record, "timeout", self.now, max_retries=1)
        now = self.after_backoff(state)
        state = next_retry_state(self.restart(state), "timeout", now, max_retries=100)
        self.assertEqual(state["retry_budget"], 1)
        self.assertEqual(check_retry(state, self.after_backoff(state), max_retries=100).decision, Decision.EXHAUSTED)
        earlier = next_retry_state(self.record, "timeout", self.now)
        tightened = next_retry_state(earlier, "timeout", self.now, max_retries=0)
        self.assertEqual(tightened["retry_budget"], 0)
        self.assertEqual(check_retry(self.restart(tightened), now, max_retries=100).decision, Decision.EXHAUSTED)

    def test_zero_budget_allows_initial_work_but_no_retry(self):
        self.assertEqual(check_retry(self.record, self.now, max_retries=0).decision, Decision.ELIGIBLE)
        state = next_retry_state(self.record, "timeout", self.now, max_retries=0)
        self.assertEqual(check_retry(state, self.now).decision, Decision.EXHAUSTED)

    def test_login_and_changed_page_require_explicit_incident_resume(self):
        for reason in ("login", "page_changed"):
            with self.subTest(reason=reason):
                state = self.restart(next_retry_state(self.record, reason, self.now))
                much_later = self.now + timedelta(days=90)
                decision = check_retry(state, much_later)
                self.assertEqual(decision.decision, Decision.RESUME_REQUIRED)
                self.assertTrue(decision.browser_stopped)
                self.assertEqual(decision.reason, reason)
                self.assertIsNone(state["next_eligible_at"])
                self.assertEqual(next_retry_state(state, "timeout", much_later), state)
                resumed = self.restart(resume_retry(state, state["blocker"]["id"], "evidence-1", much_later))
                self.assertEqual(check_retry(resumed, much_later).decision, Decision.ELIGIBLE)
                self.assertFalse(check_retry(resumed, much_later).browser_stopped)
                self.assertEqual(resumed["blocker"]["resume"]["evidence_id"], "evidence-1")
                self.assertEqual(resumed["retry_counts"], {reason: 1})

    def test_resume_cannot_target_an_old_incident_or_empty_evidence(self):
        state = next_retry_state(self.record, "login", self.now)
        first_id = state["blocker"]["id"]
        with self.assertRaises(ValueError):
            resume_retry(state, first_id, "", self.now)
        with self.assertRaises(ValueError):
            resume_retry(state, first_id, "evidence-1", self.now - timedelta(seconds=1))
        resumed = resume_retry(state, first_id, "evidence-1", self.now)
        new = next_retry_state(resumed, "login", self.now)
        self.assertNotEqual(new["blocker"]["id"], first_id)
        with self.assertRaises(ValueError):
            resume_retry(new, first_id, "evidence-1", self.now)

    def test_repeated_login_failure_also_exhausts_budget(self):
        state = next_retry_state(self.record, "login", self.now, max_retries=1)
        state = resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)
        state = next_retry_state(state, "page_changed", self.now)
        self.assertEqual(check_retry(state, self.now).decision, Decision.EXHAUSTED)
        self.assertTrue(check_retry(state, self.now).browser_stopped)
        with self.assertRaises(ValueError):
            resume_retry(state, state["blocker"]["id"], "evidence-2", self.now)

    def test_unknown_answers_wait_for_all_actual_ids_without_polling_budget(self):
        state = next_retry_state(self.record, "unknown_answer", self.now, blocked_answer_ids=["answer-a", "answer-b"])
        for _ in range(20):
            state = self.restart(next_retry_state(state, "unknown_answer", self.now))
            self.assertEqual(check_retry(state, self.now + timedelta(days=365)).decision, Decision.WAITING_ANSWER)
        self.assertEqual(state["retry_counts"], {"unknown_answer": 1})
        partial = resolve_answer_blocker(state, ["unrelated", "answer-a"], self.now)
        self.assertEqual(partial["blocker"]["answer_ids"], ["answer-b"])
        self.assertEqual(check_retry(partial, self.now).decision, Decision.WAITING_ANSWER)
        ready = resolve_answer_blocker(partial, ["answer-b"], self.now)
        self.assertEqual(check_retry(self.restart(ready), self.now).decision, Decision.ELIGIBLE)
        self.assertEqual(ready["retry_counts"], state["retry_counts"])

    def test_new_answer_facts_can_requeue_more_than_transient_budget(self):
        state = self.record
        for _ in range(8):
            state = next_retry_state(state, "unknown_answer", self.now, max_retries=0, blocked_answer_ids=["answer-a"])
            state = resolve_answer_blocker(state, ["answer-a"], self.now, max_retries=0)
            self.assertEqual(check_retry(state, self.now, max_retries=0).decision, Decision.ELIGIBLE)
        self.assertEqual(state["retry_counts"], {"unknown_answer": 8})

    def test_new_answer_blockers_merge_and_empty_blocker_is_rejected(self):
        with self.assertRaises(ValueError):
            next_retry_state(self.record, "unknown_answer", self.now)
        state = next_retry_state({**self.record, "blocked_on_answer_ids": ["answer-a"]}, "unknown_answer", self.now)
        merged = next_retry_state(state, "unknown_answer", self.now, blocked_answer_ids=["answer-b"])
        self.assertEqual(merged["blocker"]["answer_ids"], ["answer-a", "answer-b"])
        self.assertEqual(merged["retry_counts"], {"unknown_answer": 1})

    def test_answer_resolution_cannot_reset_tightened_failure_budget(self):
        state = next_retry_state(self.record, "timeout", self.now)
        now = self.after_backoff(state)
        state = next_retry_state(state, "unknown_answer", now, blocked_answer_ids=["answer-a"])
        state = resolve_answer_blocker(state, ["answer-a"], now, max_retries=0)
        self.assertEqual(check_retry(state, self.now, max_retries=100).decision, Decision.EXHAUSTED)

    def test_new_browser_stop_overrides_waits_but_preserves_answer_dependencies(self):
        state = next_retry_state(self.record, "unknown_answer", self.now, blocked_answer_ids=["answer-a"])
        stopped = next_retry_state(state, "login", self.now)
        self.assertTrue(check_retry(stopped, self.now).browser_stopped)
        resumed = resume_retry(stopped, stopped["blocker"]["id"], "evidence-1", self.now)
        self.assertEqual(check_retry(resumed, self.now).decision, Decision.WAITING_ANSWER)
        resolved = resolve_answer_blocker(resumed, ["answer-a"], self.now)
        self.assertEqual(check_retry(resolved, self.now).decision, Decision.ELIGIBLE)
        state = next_retry_state(self.record, "timeout", self.now, max_retries=0)
        stopped = next_retry_state(state, "page_changed", self.now)
        self.assertEqual(check_retry(stopped, self.now).decision, Decision.EXHAUSTED)
        self.assertTrue(check_retry(stopped, self.now).browser_stopped)

    def test_browser_stop_escalation_cannot_downgrade_safety_stop(self):
        state = next_retry_state(self.record, "login", self.now)
        state = next_retry_state(state, "account_safety", self.now)
        self.assertEqual(state["blocker"]["reason"], "account_safety")
        self.assertEqual(next_retry_state(state, "page_changed", self.now), state)
        self.assertEqual(check_retry(state, self.now).decision, Decision.STOPPED)

    def test_answer_resolution_during_browser_stop_does_not_resume_browser(self):
        state = next_retry_state(self.record, "unknown_answer", self.now, blocked_answer_ids=["answer-a"])
        state = next_retry_state(state, "page_changed", self.now)
        state = resolve_answer_blocker(state, ["answer-a"], self.now)
        self.assertEqual(check_retry(state, self.now).decision, Decision.RESUME_REQUIRED)

    def test_browser_resume_preserves_unexpired_transient_backoff(self):
        state = next_retry_state(self.record, "throttling", self.now)
        deadline = self.after_backoff(state)
        state = next_retry_state(state, "login", self.now)
        resumed = resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)
        self.assertEqual(check_retry(resumed, self.now).decision, Decision.BACKOFF)
        self.assertEqual(self.after_backoff(resumed), deadline)
        self.assertEqual(check_retry(resumed, deadline).decision, Decision.ELIGIBLE)

    def test_new_answer_dependency_during_login_remains_after_resume(self):
        state = next_retry_state(self.record, "login", self.now)
        state = next_retry_state(state, "unknown_answer", self.now, blocked_answer_ids=["answer-a"])
        self.assertEqual(check_retry(state, self.now).decision, Decision.RESUME_REQUIRED)
        resumed = resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)
        self.assertEqual(check_retry(resumed, self.now).decision, Decision.WAITING_ANSWER)

    def test_uncertain_status_or_intent_never_automatically_retries(self):
        for extra in ({"status": "submission_uncertain"}, {"submission_intent": {"attempt_id": "attempt-1"}}, {"submission_intent": {}}, {"stop_reason": "submission_uncertain"}):
            with self.subTest(extra=extra):
                source = {**self.record, **extra, "retryable_statuses": ["submission_uncertain", "failed", "ready"]}
                self.assertEqual(check_retry(source, self.now, max_retries=100).decision, Decision.RECONCILIATION_REQUIRED)
                state = self.restart(next_retry_state(source, "timeout", self.now))
                self.assertEqual(state["blocker"]["reason"], "submission_uncertain")
                state["status"] = "ready"
                state.pop("submission_intent", None)
                state.pop("stop_reason", None)
                self.assertEqual(check_retry(state, self.now + timedelta(days=365), max_retries=100).decision, Decision.RECONCILIATION_REQUIRED)
                self.assertEqual(next_retry_state(state, "throttling", self.now), state)
                self.assertEqual(resolve_answer_blocker(state, ["answer-a"], self.now), state)
                with self.assertRaises(ValueError):
                    resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)

    def test_uncertainty_overrides_active_stops_backoff_and_exhaustion(self):
        for reason in ("login", "timeout", "off_scope", "unknown_answer"):
            state = next_retry_state(self.record, reason, self.now, max_retries=0, blocked_answer_ids=["answer-a"])
            uncertain = next_retry_state(state, "submission_uncertain", self.now)
            self.assertEqual(check_retry(uncertain, self.now).decision, Decision.RECONCILIATION_REQUIRED)

    def test_confirmation_is_protected_even_when_status_is_stale(self):
        for extra in ({"status": "submitted"}, {"status": "confirmed"}, {"submissions": [{"submitted_at": self.now.isoformat()}]}, {"submitted_at": self.now.isoformat()}):
            source = {**self.record, **extra}
            state = next_retry_state(source, "timeout", self.now)
            self.assertEqual(check_retry(state, self.now).decision, Decision.CONFIRMED)
            self.assertEqual(state["retry_counts"], {})
            self.assertIsNone(state["blocker"])

    def test_terminal_stops_cannot_be_resumed_by_login_or_answers_api(self):
        for reason in ("off_scope", "unsupported", "account_safety", "manual_stop"):
            state = next_retry_state(self.record, reason, self.now)
            self.assertEqual(check_retry(state, self.now).decision, Decision.STOPPED)
            self.assertEqual(next_retry_state(state, "timeout", self.now), state)
            with self.assertRaises(ValueError):
                resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)
            self.assertEqual(resolve_answer_blocker(state, ["answer-a"], self.now), state)

    def test_independent_record_is_eligible_during_other_record_backoff(self):
        blocked = next_retry_state(self.record, "throttling", self.now)
        self.assertEqual(check_retry(blocked, self.now).decision, Decision.BACKOFF)
        self.assertEqual(check_retry(self.record, self.now).decision, Decision.ELIGIBLE)

    def test_transitions_and_checks_do_not_mutate_nested_input(self):
        before = deepcopy(self.record)
        state = next_retry_state(self.record, "login", self.now)
        self.assertEqual(self.record, before)
        state["events"].append({"reason": "synthetic"})
        before = deepcopy(state)
        resumed = resume_retry(state, state["blocker"]["id"], "evidence-1", self.now)
        check_retry(resumed, self.now)
        self.assertEqual(state, before)
        unknown = next_retry_state(self.record, "unknown_answer", self.now, blocked_answer_ids=["answer-a", "answer-b"])
        before = deepcopy(unknown)
        resolve_answer_blocker(unknown, ["answer-a"], self.now)
        self.assertEqual(unknown, before)

    def test_provider_error_text_is_neither_accepted_nor_reflected(self):
        marker = "SYNTHETIC_PRIVATE_TOKEN https://example.test/?secret=canary"
        with self.assertRaises(ValueError) as caught:
            next_retry_state(self.record, marker, self.now)
        self.assertNotIn(marker, str(caught.exception))
        state = next_retry_state(self.record, Reason.LOGIN, self.now)
        with self.assertRaises(ValueError) as caught:
            resume_retry(state, state["blocker"]["id"], marker, self.now)
        self.assertNotIn(marker, str(caught.exception))
        result = json.dumps(check_retry(state, self.now).as_dict())
        self.assertNotIn(marker, result)
        self.assertEqual(json.loads(result), {"decision": "resume_required", "reason": "login", "next_eligible_at": None, "browser_stopped": True})

    def test_invalid_clocks_limits_and_partial_state_fail_closed(self):
        for now in (datetime(2026, 10, 20), "invalid private text", "2026-10-20T00:00:00"):
            with self.assertRaises(ValueError):
                check_retry(self.record, now)
        for maximum in (-1, True, 1.5, float("inf")):
            with self.assertRaises(ValueError):
                next_retry_state(self.record, "timeout", self.now, max_retries=maximum)
        for seconds in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                next_retry_state(self.record, "timeout", self.now, backoff_seconds=seconds)
        state = next_retry_state(self.record, "timeout", self.now)
        for field in ("retry_budget", "retry_counts", "next_eligible_at", "retry_policy_version"):
            broken = deepcopy(state)
            broken.pop(field)
            with self.assertRaises(ValueError):
                check_retry(broken, self.now)
        for patch in ({"retry_counts": {"timeout": -1}}, {"retry_counts": {"raw provider error": 1}}, {"next_eligible_at": None}, {"retry_policy_version": True}):
            with self.assertRaises(ValueError):
                check_retry({**state, **patch}, self.now)


if __name__ == "__main__":
    unittest.main()
