"""Pure, durable retry decisions. The caller owns persistence and browser actions."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import re


class Reason(str, Enum):
    UNKNOWN_ANSWER = "unknown_answer"
    LOGIN = "login"
    PAGE_CHANGED = "page_changed"
    THROTTLING = "throttling"
    TIMEOUT = "timeout"
    SUBMISSION_UNCERTAIN = "submission_uncertain"
    OFF_SCOPE = "off_scope"
    ACCOUNT_SAFETY = "account_safety"
    MANUAL_STOP = "manual_stop"
    UNSUPPORTED = "unsupported"


class Decision(str, Enum):
    ELIGIBLE = "eligible"
    BACKOFF = "backoff"
    WAITING_ANSWER = "waiting_answer"
    RESUME_REQUIRED = "resume_required"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    EXHAUSTED = "exhausted"
    CONFIRMED = "confirmed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class RetryDecision:
    decision: Decision
    reason: Reason | None = None
    next_eligible_at: str | None = None
    browser_stopped: bool = False

    def as_dict(self):
        """Only static codes, a timestamp and a boolean; no provider text."""
        return asdict(self)


MAX_BACKOFF_SECONDS = 3600
_TRANSIENT = {Reason.TIMEOUT, Reason.THROTTLING}
_RESUMABLE = {Reason.LOGIN, Reason.PAGE_CHANGED}
_BUDGETED = _TRANSIENT | _RESUMABLE
_BROWSER_STOPS = _RESUMABLE | {Reason.ACCOUNT_SAFETY, Reason.MANUAL_STOP}
_FIELDS = {"retry_policy_version", "retry_budget", "retry_counts", "blocker", "next_eligible_at"}


def _reason(value):
    try:
        return Reason(value)
    except (ValueError, TypeError):
        raise ValueError("Unknown retry reason; use a Reason code") from None


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Retry limits and counts must be non-negative integers")
    return value


def _time(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Retry timestamps must include a UTC offset") from None


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Retry evidence and answer IDs must be opaque identifiers")
    return value


def _ids(values):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("Answer IDs must be a collection of opaque identifiers")
    return sorted({_identifier(value) for value in values})


def _state(record, max_retries):
    budget = _integer(max_retries)
    if not isinstance(record, dict):
        raise ValueError("Retry record must be an object")
    state = deepcopy(record)
    _ids(state.get("blocked_on_answer_ids", []))
    if not _FIELDS.intersection(state):
        state.update(retry_policy_version=1, retry_budget=budget, retry_counts={},
                     blocker=None, next_eligible_at=None)
        return state
    if not _FIELDS.issubset(state) or type(state["retry_policy_version"]) is not int or state["retry_policy_version"] != 1:
        raise ValueError("Incomplete or unsupported retry state; preserve it for review")
    state["retry_budget"] = min(budget, _integer(state["retry_budget"]))
    counts = state["retry_counts"]
    if not isinstance(counts, dict):
        raise ValueError("Retry counts must be an object")
    for reason, count in counts.items():
        _reason(reason)
        _integer(count)
    blocker = state["blocker"]
    if blocker is not None:
        if not isinstance(blocker, dict) or set(blocker) != {"id", "reason", "blocked_at", "answer_ids", "resume"}:
            raise ValueError("Invalid typed retry blocker")
        _identifier(blocker["id"])
        reason = _reason(blocker["reason"])
        _time(blocker["blocked_at"])
        ids = _ids(blocker["answer_ids"])
        if (reason == Reason.UNKNOWN_ANSWER) != bool(ids):
            raise ValueError("Only unknown-answer blockers require answer IDs")
        resume = blocker["resume"]
        if resume is not None:
            if reason not in _RESUMABLE or not isinstance(resume, dict) or set(resume) != {"evidence_id", "at"}:
                raise ValueError("Invalid explicit retry resume")
            _identifier(resume["evidence_id"])
            if _time(resume["at"]) < _time(blocker["blocked_at"]):
                raise ValueError("Resume evidence predates the blocker")
    deadline = state["next_eligible_at"]
    if deadline is not None:
        if blocker is None or _reason(blocker["reason"]) not in _BUDGETED:
            raise ValueError("Only transient or resumable blockers have a retry deadline")
        if _time(deadline) < _time(blocker["blocked_at"]):
            raise ValueError("Retry deadline predates the blocker")
    elif blocker is not None and _reason(blocker["reason"]) in _TRANSIENT:
        raise ValueError("Transient retry deadline is missing")
    return state


def _confirmed(record):
    return bool(record.get("submissions") or record.get("submitted_at") or
                record.get("status") in {"submitted", "confirmed"})


def _uncertain(record):
    return (record.get("status") == "submission_uncertain" or
            record.get("submission_intent") is not None or
            record.get("stop_reason") == Reason.SUBMISSION_UNCERTAIN or
            (record.get("blocker") or {}).get("reason") == Reason.SUBMISSION_UNCERTAIN)


def _used(state):
    # One budget across transient and browser-resume failures; changing reasons
    # or restarting cannot buy additional failure-driven retries.
    return sum(state["retry_counts"].get(reason.value, 0) for reason in _BUDGETED)


def check_retry(record, now, max_retries=3):
    """Return a typed eligibility decision. Never mutate, sleep, retry or click."""
    now = _time(now)
    state = _state(record, max_retries)
    if _confirmed(state):
        return RetryDecision(Decision.CONFIRMED)
    if _uncertain(state):
        return RetryDecision(Decision.RECONCILIATION_REQUIRED, Reason.SUBMISSION_UNCERTAIN)
    blocker = state["blocker"]
    reason = _reason(blocker["reason"]) if blocker else None
    stopped = bool(blocker and reason in _BROWSER_STOPS and blocker["resume"] is None)
    if reason is not None and reason not in _BUDGETED and reason != Reason.UNKNOWN_ANSWER:
        return RetryDecision(Decision.STOPPED, reason, browser_stopped=stopped)
    if _used(state) > state["retry_budget"]:
        return RetryDecision(Decision.EXHAUSTED, reason, browser_stopped=stopped)
    if stopped:
        return RetryDecision(Decision.RESUME_REQUIRED, reason, browser_stopped=True)
    if reason == Reason.UNKNOWN_ANSWER or state.get("blocked_on_answer_ids"):
        return RetryDecision(Decision.WAITING_ANSWER, Reason.UNKNOWN_ANSWER)
    if blocker is None:
        return RetryDecision(Decision.ELIGIBLE)
    deadline = state["next_eligible_at"]
    if deadline is not None and now < _time(deadline):
        return RetryDecision(Decision.BACKOFF, reason, deadline)
    return RetryDecision(Decision.ELIGIBLE, reason)


def next_retry_state(record, reason, now, max_retries=3, backoff_seconds=60, *, blocked_answer_ids=None):
    """Record one observed failure, returning a deep copy with durable fields.

    Poll with check_retry instead. An active stop/wait/backoff is idempotent;
    a new failure can be recorded after eligibility or explicit resolution.
    """
    reason, now = _reason(reason), _time(now)
    if type(backoff_seconds) is not int or backoff_seconds <= 0:
        raise ValueError("Backoff must be a positive integer number of seconds")
    state = _state(record, max_retries)
    if _confirmed(state):
        return state
    if _uncertain(state):
        reason = Reason.SUBMISSION_UNCERTAIN
    ids = []
    if reason == Reason.UNKNOWN_ANSWER:
        ids = _ids(blocked_answer_ids if blocked_answer_ids is not None else record.get("blocked_on_answer_ids", []))
        ids = sorted(set(ids) | set(state.get("blocked_on_answer_ids", [])))
        if not ids:
            raise ValueError("Unknown-answer blockers require the actual unresolved answer IDs")
        state["blocked_on_answer_ids"] = ids[:]
        if (state["blocker"] or {}).get("reason") == Reason.UNKNOWN_ANSWER:
            state["blocker"]["answer_ids"] = sorted(set(state["blocker"]["answer_ids"]) | set(ids))
    previous = check_retry(state, now, max_retries)
    existing_reason = (state["blocker"] or {}).get("reason")
    # A newly observed browser stop must survive even during answer waits or
    # backoff. It cannot downgrade an existing safety/manual stop.
    escalate = (reason in _BROWSER_STOPS and
                existing_reason not in {Reason.ACCOUNT_SAFETY, Reason.MANUAL_STOP} and
                not (reason in _RESUMABLE and previous.browser_stopped))
    answer_stop = (reason == Reason.UNKNOWN_ANSWER and state["blocker"] is None and
                   previous.decision == Decision.WAITING_ANSWER)
    if reason != Reason.SUBMISSION_UNCERTAIN and not escalate and not answer_stop and previous.decision != Decision.ELIGIBLE:
        return state
    if reason == Reason.SUBMISSION_UNCERTAIN and (state["blocker"] or {}).get("reason") == reason:
        return state
    counts = state["retry_counts"]
    counts[reason.value] = counts.get(reason.value, 0) + 1
    deadline = state["next_eligible_at"]
    state["blocker"] = {"id": "retry-" + str(sum(counts.values())), "reason": reason.value,
                        "blocked_at": now.isoformat(), "answer_ids": ids, "resume": None}
    state["next_eligible_at"] = None
    if reason in _RESUMABLE and deadline is not None and _time(deadline) > now:
        state["next_eligible_at"] = deadline
    if reason in _TRANSIENT:
        delay = min(MAX_BACKOFF_SECONDS, min(backoff_seconds, MAX_BACKOFF_SECONDS) * 2 ** min(_used(state) - 1, 12))
        # UTC arithmetic preserves elapsed delay across daylight-saving changes.
        deadline = now.astimezone(timezone.utc) + timedelta(seconds=delay)
        state["next_eligible_at"] = deadline.astimezone(now.tzinfo).isoformat()
    return state


def resume_retry(record, blocker_id, evidence_id, now, max_retries=3):
    """Explicitly resume this login/page-change incident using verified evidence.

    The parent validates/saves evidence privately; an ID is not proof by itself.
    Other stops and submission uncertainty cannot be cleared by this API.
    """
    now = _time(now)
    _identifier(evidence_id)
    state = _state(record, max_retries)
    blocker = state["blocker"]
    if (check_retry(state, now, max_retries).decision != Decision.RESUME_REQUIRED or
            blocker is None or blocker["id"] != blocker_id):
        raise ValueError("Resume requires the current login or page-change blocker")
    if now < _time(blocker["blocked_at"]):
        raise ValueError("Resume evidence predates the blocker")
    blocker["resume"] = {"evidence_id": evidence_id, "at": now.isoformat()}
    return state


def resolve_answer_blocker(record, resolved_answer_ids, now, max_retries=3):
    """Apply only IDs the parent's scoped, fresh answer resolver has confirmed.

    Unrelated/partial resolutions keep waiting; this is not a polling counter.
    Answer-driven requeues never consume or reset the failure retry budget.
    """
    now = _time(now)
    resolved = set(_ids(resolved_answer_ids))
    state = _state(record, max_retries)
    blocker = state["blocker"]
    if _confirmed(state) or _uncertain(state):
        return state
    if blocker is not None and now < _time(blocker["blocked_at"]):
        raise ValueError("Answer resolution predates the blocker")
    if "blocked_on_answer_ids" in state:
        state["blocked_on_answer_ids"] = sorted(set(state["blocked_on_answer_ids"]) - resolved)
    if blocker is not None and blocker["reason"] == Reason.UNKNOWN_ANSWER:
        blocker["answer_ids"] = sorted(set(blocker["answer_ids"]) - resolved)
        if not blocker["answer_ids"]:
            state["blocker"] = None
    return state
