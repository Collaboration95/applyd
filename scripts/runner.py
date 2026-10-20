"""Small durable next-action controller. Browser and workspace gates are injected."""

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import re
from typing import Callable, ContextManager
from zoneinfo import ZoneInfo

import batches
import retry_policy
import settings


class Kind(str, Enum):
    DISCOVER = "discover"
    PREPARE = "prepare"
    REVIEW = "review"
    SUBMIT = "submit"
    HANDOFF = "manual_handoff"
    COMPLETE = "complete"


@dataclass(frozen=True)
class Action:
    kind: Kind
    run_id: str
    sequence: int
    application_id: str | None = None
    record_ref: str | None = None
    remaining: int | None = None
    reason: str | None = None

    def as_dict(self):
        return {**asdict(self), "kind": self.kind.value}


@dataclass(frozen=True)
class Queue:
    """Parent loads complete records and the selected existing batch brief."""
    records: list[dict]
    batch_id: str
    target: int
    category_targets: dict[str, int] | None = None


@dataclass(frozen=True)
class Observation:
    """Only artifact references/static reason codes cross the adapter boundary."""
    evidence_ref: str | None = None
    failure: retry_policy.Reason | None = None


@dataclass(frozen=True)
class Verification:
    # verified advances checkpoint; not_executed releases pending with evidence;
    # unresolved retains pending and forbids replay. The parent validates evidence.
    disposition: str
    evidence_ref: str | None = None


@dataclass(frozen=True)
class Hooks:
    writer: Callable[[], ContextManager]
    recover: Callable[[str, datetime], None]
    queue: Callable[[str, datetime], Queue]
    guard: Callable[[Action, datetime], None]
    begin_submit: Callable[[Action, datetime], bool]
    verify: Callable[[Action, Observation | None, datetime], Verification]
    failure: Callable[[Action, retry_policy.Reason, datetime], None]


class Runner:
    def __init__(self, root, run_id, *, hooks=None, adapter=None):
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("Invalid runner ID")
        self.root = Path(root).resolve()
        self.run_id, self.hooks, self.adapter = run_id, hooks, adapter
        self.state_ref = f"applications/runs/{run_id}/state.json"

    def _path(self, ref):
        if (not isinstance(ref, str) or Path(ref).is_absolute() or ".." in Path(ref).parts
                or not ref.startswith("applications/")):
            raise ValueError("Invalid runner artifact reference")
        path = self.root / ref
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Runner artifact escapes workspace")
        return path

    def _load(self):
        path = self._path(self.state_ref)
        if not path.exists():
            if path.is_symlink():
                raise ValueError("Invalid runner state")
            return {"schema_version": 1, "run_id": self.run_id, "sequence": 0,
                    "plan": None, "pending": None, "checkpoint": None,
                    "batch": None, "decisions": {}, "last_result": None}
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or set(state) != {"schema_version", "run_id", "sequence",
                "plan", "pending", "checkpoint", "batch", "decisions", "last_result"}
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or state["run_id"] != self.run_id or type(state["sequence"]) is not int
                or state["sequence"] < 0 or not isinstance(state["decisions"], dict)):
            raise ValueError("Invalid runner state; preserve for reconciliation")
        for field in ("plan", "pending", "checkpoint"):
            saved = state[field]
            if saved is not None:
                if not isinstance(saved, dict) or "action" not in saved:
                    raise ValueError("Invalid runner checkpoint")
                action = self._action(saved["action"])
                if field in {"pending", "checkpoint"}:
                    if action.kind in {Kind.HANDOFF, Kind.COMPLETE} or action.sequence > state["sequence"]:
                        raise ValueError("Invalid runner checkpoint sequence")
                    batches.validate_event_time(saved["started_at" if field == "pending" else "verified_at"])
                    if field == "pending" and action.sequence != state["sequence"]:
                        raise ValueError("Invalid runner pending sequence")
                    if field == "checkpoint":
                        self._path(saved["evidence_ref"])
        return state

    def _action(self, data):
        action = Action(**{**data, "kind": Kind(data["kind"])})
        if (action.run_id != self.run_id or type(action.sequence) is not int or action.sequence < 1
                or (action.application_id is not None and
                    not re.fullmatch(r"[a-z0-9][a-z0-9-]*", action.application_id))):
            raise ValueError("Invalid saved runner action")
        expected = (f"applications/records/{action.application_id}/record.json"
                    if action.application_id else None)
        if action.record_ref != expected:
            raise ValueError("Invalid saved runner record reference")
        if ((action.kind in {Kind.PREPARE, Kind.REVIEW, Kind.SUBMIT} and not action.application_id)
                or (action.kind == Kind.DISCOVER and action.application_id)):
            raise ValueError("Invalid saved runner action identity")
        return action

    def _save(self, state):
        # This is deliberately the only runner persistence target. Never run.json.
        from workspace import atomic_json

        atomic_json(self._path(self.state_ref), state)

    def _new(self, state, kind, app=None, remaining=None, reason=None):
        return self._action({"kind": kind, "run_id": self.run_id,
            "sequence": state["sequence"] + 1, "application_id": app,
            "record_ref": f"applications/records/{app}/record.json" if app else None,
            "remaining": remaining, "reason": reason})

    def _policy(self, state, queue, now):
        """Use existing accounting/identity semantics, never a mutable quota counter."""
        current = settings.load_settings(self.root)
        run = settings.load_run(self.root, self.run_id)
        frozen = settings.validate_settings(run["settings"])
        # Batch targets come from the selected brief, never from agent context.
        # Freeze them separately from settings; later edits may only tighten them.
        batches.batch_progress(queue.records, queue.batch_id, queue.target, queue.category_targets)
        if state["batch"] is None:
            state["batch"] = {"id": queue.batch_id, "target": queue.target,
                              "categories": dict(queue.category_targets or {})}
        batch = state["batch"]
        if batch["id"] != queue.batch_id:
            raise ValueError("Runner batch changed; start a new run")
        categories = dict(queue.category_targets or {})
        old = batch["categories"]
        if old and categories:
            categories = {key: min(old.get(key, 0), categories.get(key, 0)) for key in old.keys() | categories.keys()}
        else:
            categories = old or categories
        progress = batches.batch_progress(queue.records, queue.batch_id, min(batch["target"], queue.target), categories)
        state["batch"] = {"id": batch["id"], "target": min(batch["target"], queue.target), "categories": categories}
        remaining = progress["remaining"]
        reason = "quota_reached" if remaining == 0 else None
        # Reuse the same transitive groups as batch accounting, including aliases
        # without confirmations. Settings count even legacy timestamps conservatively.
        groups = batches._groups(queue.records)
        confirmations = [[event for record in group for event in settings._confirmed(record)] for group in groups]
        start = batches.validate_event_time(run["started_at"])
        for policy in (current, frozen):
            if now < start:
                reason = "run_not_started"
            deadline = settings._deadline(start, policy)
            if deadline is not None and now >= deadline:
                reason = "deadline_reached"
            zone = ZoneInfo(policy["timezone"])
            day = now.astimezone(zone).date()
            daily = sum(any(at.astimezone(zone).date() == day for at, _ in events) for events in confirmations)
            per_run = sum(any(rid == self.run_id for _, rid in events) for events in confirmations)
            for key, count in (("daily_cap", daily), ("per_run_cap", per_run)):
                if policy[key] is not None:
                    remaining = min(remaining, max(0, policy[key] - count))
                    if remaining == 0:
                        reason = "quota_reached"
            # The small initial runner delegates save-only/dry-run explicitly.
            if policy["mode"] != "apply":
                reason = "mode_requires_manual_handoff"
        return current, frozen, groups, progress, remaining, reason

    def _verified(self, state, action, observation, now):
        verdict = self.hooks.verify(action, observation, now)
        if not isinstance(verdict, Verification) or verdict.disposition not in {"verified", "not_executed", "unresolved"}:
            raise ValueError("Invalid runner verification")
        if verdict.disposition == "unresolved":
            return "unresolved"
        path = self._path(verdict.evidence_ref)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Runner verification requires saved nonempty evidence")
        if verdict.disposition == "verified" and action.kind == Kind.SUBMIT:
            queue = self.hooks.queue(self.run_id, now)
            progress = batches.batch_progress(queue.records, queue.batch_id, queue.target, queue.category_targets)
            if action.application_id not in progress["exclusions"]["confirmed"]:
                raise ValueError("Submit verification requires a durable confirmation")
        if verdict.disposition == "verified":
            state["checkpoint"] = {"action": action.as_dict(), "verified_at": now.isoformat(),
                                   "evidence_ref": verdict.evidence_ref}
        state["pending"] = None
        state["last_result"] = verdict.disposition
        self._save(state)
        return verdict.disposition

    def _next(self, now):
        state = self._load()
        # Recover existing browser claims before considering ANY further work.
        # Historical verification is allowed even when new-work gates now refuse.
        self.hooks.recover(self.run_id, now)
        if state["pending"] is not None:
            pending = self._action(state["pending"]["action"])
            if self._verified(state, pending, None, now) == "unresolved":
                return state, self._new(state, Kind.HANDOFF, pending.application_id,
                    reason="submission_uncertain" if pending.kind == Kind.SUBMIT else "unverified_action")
        queue = self.hooks.queue(self.run_id, now)
        current, frozen, groups, progress, remaining, reason = self._policy(state, queue, now)
        if reason:
            kind = Kind.COMPLETE if reason == "quota_reached" else Kind.HANDOFF
            action = self._new(state, kind, remaining=remaining, reason=reason)
        else:
            candidates = []
            decisions = {}
            for group in groups:
                checks = [(r, retry_policy.check_retry(r, now)) for r in group]
                # Stronger than batch credit: even malformed/unproven confirmation
                # claims deny replay, and an empty intent is still uncertainty.
                blocked = next((d.decision.value for _, d in checks if d.decision in {
                    retry_policy.Decision.CONFIRMED, retry_policy.Decision.RECONCILIATION_REQUIRED}), None)
                for record, decision in checks:
                    app = record["application_id"]
                    decisions[app] = blocked or decision.decision.value
                if blocked:
                    continue
                eligible = []
                for record, decision in checks:
                    if decision.decision != retry_policy.Decision.ELIGIBLE:
                        continue
                    status = record.get("status")
                    retryable = status in current["retryable_statuses"] and status in frozen["retryable_statuses"]
                    if status not in {"discovered", "preparing", "ready"} and not retryable:
                        continue
                    category = progress["categories"].get(record.get("category"))
                    if progress["categories"] and (category is None or category["remaining"] == 0):
                        decisions[record["application_id"]] = "category_quota"
                        continue
                    resolved = record.get("resumed_from_answers") is True
                    eligible.append((0 if resolved else 1, record["application_id"], record))
                if eligible:
                    candidates.append(min(eligible, key=lambda item: item[:2]))
            state["decisions"] = decisions
            if candidates:
                _, app, record = min(candidates, key=lambda item: item[:2])
                kind = (Kind.SUBMIT if (record.get("prepared") or {}).get("review_snapshot")
                        else Kind.REVIEW) if record["status"] == "ready" else Kind.PREPARE
                action = self._new(state, kind, app, remaining)
            else:
                action = self._new(state, Kind.DISCOVER, remaining=remaining)
        state["plan"] = {"action": action.as_dict(), "planned_at": now.isoformat()}
        self._save(state)
        return state, action

    def _manual(self, action, reason):
        return Action(Kind.HANDOFF, self.run_id, action.sequence, action.application_id,
                      action.record_ref, action.remaining, reason)

    def plan_next(self, now):
        """Persist one next action without requiring an execution adapter."""
        now = batches.validate_event_time(now)
        if self.hooks is None:
            return Action(Kind.HANDOFF, self.run_id, 1, reason="callbacks_missing")
        with self.hooks.writer():
            _, action = self._next(now)
            return action

    def next_action(self, now: datetime) -> Action:
        """Persist a plan only; never invoke the adapter or reserve a submit."""
        now = batches.validate_event_time(now)
        if self.hooks is None:
            return Action(Kind.HANDOFF, self.run_id, 1, reason="callbacks_missing")
        with self.hooks.writer():
            state, action = self._next(now)
            if self.adapter is None and action.kind not in {Kind.HANDOFF, Kind.COMPLETE}:
                state["last_result"] = "adapter_missing"
                self._save(state)
                return self._manual(action, "adapter_missing")
            return action

    def execute(self, *, clock: Callable[[], datetime], max_steps: int = 1) -> list[Action]:
        """At most max_steps adapter calls, serialized through the parent's writer."""
        if type(max_steps) is not int or not 0 <= max_steps <= 100:
            raise ValueError("max_steps must be an integer from 0 to 100")
        results = []
        for _ in range(max_steps):
            now = batches.validate_event_time(clock())
            if self.hooks is None:
                results.append(Action(Kind.HANDOFF, self.run_id, 1, reason="callbacks_missing"))
                break
            with self.hooks.writer():
                state, action = self._next(now)
                if action.kind in {Kind.COMPLETE, Kind.HANDOFF}:
                    results.append(action)
                    break
                if self.adapter is None:
                    state["last_result"] = "adapter_missing"
                    self._save(state)
                    results.append(self._manual(action, "adapter_missing"))
                    break
                try:
                    # Mandatory parent checks include safety/session retry,
                    # eligibility, resume/answer freshness and policy/identity.
                    now = batches.validate_event_time(clock())
                    if self.hooks.guard(action, now) is not None:
                        raise ValueError("Runner guard must explicitly succeed with None")
                except Exception:
                    state["last_result"] = "gate_blocked"
                    self._save(state)
                    results.append(self._manual(action, "gate_blocked"))
                    break
                state.update(sequence=action.sequence, pending={"action": action.as_dict(), "started_at": now.isoformat()},
                             last_result="pending")
                self._save(state)  # Failure here must prevent ALL adapter calls.
                failure_reported = False
                reason = retry_policy.Reason.SUBMISSION_UNCERTAIN if action.kind == Kind.SUBMIT else retry_policy.Reason.UNSUPPORTED
                try:
                    if action.kind == Kind.SUBMIT and self.hooks.begin_submit(action, now) is not True:
                        raise ValueError("Submit was not authorized")
                    observation = self.adapter(action)
                    if not isinstance(observation, Observation):
                        raise ValueError("Invalid runner observation")
                    if observation.failure is not None:
                        reason = (retry_policy.Reason.SUBMISSION_UNCERTAIN if action.kind == Kind.SUBMIT
                                  else retry_policy.Reason(observation.failure))
                        # A callback can commit its mutation and then fail while
                        # exporting. Never report the same observation twice.
                        failure_reported = True
                        self.hooks.failure(action, reason, now)
                    verified = self._verified(state, action, observation, batches.validate_event_time(clock()))
                except Exception:
                    # Do not store provider text or automatically replay any step.
                    # A verified checkpoint write may itself have failed. Restore
                    # the last durable state rather than clearing its pending step.
                    state = self._load()
                    state["last_result"] = reason.value
                    self._save(state)
                    if not failure_reported:
                        self.hooks.failure(action, reason, batches.validate_event_time(clock()))
                    results.append(self._manual(action, reason.value))
                    break
                results.append(action if verified == "verified" else self._manual(action,
                    "unverified_action" if verified == "unresolved" else "not_executed"))
                if verified != "verified":
                    break
        return results
