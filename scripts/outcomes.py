"""Pure helpers for application outcome events and follow-up dates."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone


OUTCOME_STATUSES = frozenset({
    "acknowledged",
    "assessment",
    "interview",
    "rejected",
    "offer",
    "withdrawn",
})
RESPONSE_STATUSES = OUTCOME_STATUSES - {"withdrawn"}


def responded(record):
    return (record.get("outcome") in RESPONSE_STATUSES or record.get("status") in RESPONSE_STATUSES
            or any(e.get("status") in RESPONSE_STATUSES for e in record.get("outcome_events", [])))


def _timestamp(value, field):
    """Parse a strict ISO-8601 timestamp that includes a UTC offset."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with an offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with an offset") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed


def _confirmed_submission_time(record):
    """Return the timestamp of an existing confirmed submission."""
    submissions = record.get("submissions")
    if isinstance(submissions, list):
        for submission in submissions:
            if isinstance(submission, dict) and submission.get("submitted_at"):
                return _timestamp(submission["submitted_at"], "submitted_at")
    if record.get("submitted_at"):
        return _timestamp(record["submitted_at"], "submitted_at")
    raise ValueError("outcomes require a confirmed original submission")


def apply_outcome(record, status, occurred_at, recorded_at, evidence=None, note="",
                  follow_up_at=None, follow_up_days=14):
    """Return a copy of a submitted record with an idempotent outcome event.

    ``occurred_at`` is when the employer response happened; ``recorded_at`` is
    when it was entered. Neither value changes submission history or grouping.
    """
    if status not in OUTCOME_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(OUTCOME_STATUSES))}")
    _confirmed_submission_time(record)
    occurred = _timestamp(occurred_at, "occurred_at")
    recorded = _timestamp(recorded_at, "recorded_at")
    follow_up = _timestamp(follow_up_at, "follow_up_at") if follow_up_at is not None else None
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    if not isinstance(follow_up_days, int) or isinstance(follow_up_days, bool) or follow_up_days < 0:
        raise ValueError("follow_up_days must be a non-negative integer")

    event = {"status": status, "occurred_at": occurred_at, "recorded_at": recorded_at}
    if evidence is not None:
        event["evidence"] = deepcopy(evidence)
    if note:
        event["note"] = note
    if follow_up is None and status not in {"rejected", "withdrawn"}:
        follow_up = occurred + timedelta(days=follow_up_days)
        event["follow_up_at"] = follow_up.isoformat()
    elif follow_up is not None:
        event["follow_up_at"] = follow_up_at
    event["follow_up_days"] = follow_up_days

    result = deepcopy(record)
    events = result.setdefault("outcome_events", [])
    if not isinstance(events, list):
        raise ValueError("outcome_events must be a list")
    # The exact same recorded event is a harmless retry.
    identity = {k: v for k, v in event.items() if k != "recorded_at"}
    if not any({k: v for k, v in saved.items() if k != "recorded_at"} == identity for saved in events):
        events.append(event)

    latest = max(events, key=lambda item: _timestamp(item["occurred_at"], "occurred_at"))
    result["outcome"] = latest["status"]
    result["latest_outcome_at"] = latest["occurred_at"]
    # Follow-up is separate from outcome occurrence time and follows the
    # event with the latest occurrence time.
    latest_follow_up = latest.get("follow_up_at")
    if latest_follow_up is None:
        result.pop("follow_up_at", None)
    else:
        result["follow_up_at"] = latest_follow_up
    return result


def overdue(record, now, days=14):
    """Return whether a response or scheduled follow-up is due by ``now``.

    An explicit ``follow_up_at`` controls the due date. Otherwise a record
    without an outcome becomes overdue ``days`` after its original submission.
    """
    current = _timestamp(now, "now")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError("days must be a non-negative integer")
    if record.get("follow_up_at") is not None:
        due = _timestamp(record["follow_up_at"], "follow_up_at")
    elif not record.get("outcome"):
        try:
            due = _confirmed_submission_time(record) + timedelta(days=days)
        except ValueError:
            return False  # No timestamp means no measured follow-up age.
    else:
        return False
    return current.astimezone(timezone.utc) >= due.astimezone(timezone.utc)
