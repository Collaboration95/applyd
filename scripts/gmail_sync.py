"""Pure, conservative planning for read-only email outcome sync.

This module does not connect to a mailbox or mutate application records. Callers
can persist proposals with mail_import.sync_local under the workspace writer lock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping, Protocol, Sequence
import re


OUTCOMES = {
    "acknowledged",
    "assessment",
    "interview",
    "rejected",
    "recruiter_message",
    "unrelated",
}

# Full, conservative phrases. Generic words such as "update" or "application"
# are intentionally not sufficient on their own.
_CUES = {
    "acknowledged": (
        "we received your application",
        "your application has been received",
        "application received",
        "thank you for applying",
        "thank you for your application",
        "application confirmation",
    ),
    "assessment": (
        "online assessment",
        "complete the assessment",
        "complete this assessment",
        "coding assessment",
        "coding test",
        "take-home assessment",
        "take home assessment",
        "technical assessment",
    ),
    "interview": (
        "invite you to interview",
        "invited to interview",
        "interview invitation",
        "schedule an interview",
        "schedule your interview",
        "interview availability",
        "interview with",
    ),
    "rejected": (
        "not moving forward with your application",
        "will not be moving forward",
        "not be proceeding with your application",
        "regret to inform you",
        "application was unsuccessful",
        "your application has been unsuccessful",
        "we have decided not to proceed",
        "not selected for the role",
    ),
    "recruiter_message": (
        "i am a recruiter",
        "i'm a recruiter",
        "i am reaching out about an opportunity",
        "reaching out about an opportunity",
        "would you be interested in discussing a role",
        "your profile was recommended to me",
    ),
}

# Positive-looking cue words negated in a different sentence are ambiguous.
_NEGATION_PATTERNS = (
    r"\b(?:did not|didn't|have not|haven't|has not|hasn't|was not|wasn't|is not|isn't|no)\b"
    r"[^.!?]{0,48}\b(?:receive|received|application|assessment|interview|selected|proceed|moving forward)\b",
    r"\b(?:not required|not needed|no longer required)\b[^.!?]{0,32}\b(?:assessment|interview|test)\b",
)
_TOKEN_RE = re.compile(r"[^a-z0-9]+")


class ReadOnlyMailAdapter(Protocol):
    """Optional adapter contract. Implementations expose reads only."""

    def search(self, *, since: str | None) -> Iterable[str]: ...

    def list(self, message_ids: Sequence[str]) -> Iterable[Mapping[str, object]]: ...

    def get(self, message_id: str) -> Mapping[str, object]: ...


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _normalized(value: object) -> str:
    return _TOKEN_RE.sub(" ", _text(value).casefold()).strip()


def _timestamp(value: object) -> datetime:
    raw = _text(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("received_at must be ISO-8601 with an explicit UTC offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("received_at must be ISO-8601 with an explicit UTC offset")
    return parsed


def classify_message(message: Mapping[str, object]) -> dict[str, object]:
    """Return a category and exact cues; uncertain/conflicting cues need review."""
    corpus = " ".join(
        _text(message.get(key)) for key in ("subject", "body")
    ).casefold()
    hits = {
        category: [cue for cue in cues if cue in corpus]
        for category, cues in _CUES.items()
    }
    matched = {category: cues for category, cues in hits.items() if cues}
    # Rejection cues are intentionally phrased with negation ("not moving
    # forward"). Treat those exact cues as rejection evidence, while still
    # flagging negated acknowledgement/assessment/interview language.
    negated = (
        not hits["rejected"]
        and any(re.search(pattern, corpus) for pattern in _NEGATION_PATTERNS)
    )
    if negated or len(matched) > 1:
        return {
            "category": None,
            "needs_review": True,
            "matched_cues": matched,
            "reason": "negated_outcome_cue" if negated else "conflicting_outcome_cues",
        }
    if matched:
        category, cues = next(iter(matched.items()))
        return {"category": category, "needs_review": False, "matched_cues": cues}
    return {
        "category": "unrelated",
        "needs_review": False,
        "matched_cues": [],
    }


def _record_id(record: Mapping[str, object]) -> str:
    return _text(record.get("application_id") or record.get("id"))


def confirmed_submission_times(record: Mapping[str, object]) -> list[datetime]:
    """Require confirmation metadata, never infer submission from status alone.

    The filesystem importer additionally verifies each file's bytes/hash. This
    pure planner can only validate the supplied confirmation metadata.
    """
    entries = [record, *(record.get("submissions") or [])]
    times = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if not (_text(entry.get("confirmation_path"))
                and re.fullmatch(r"[a-fA-F0-9]{64}", _text(entry.get("confirmation_sha256")))):
            continue
        try:
            times.append(_timestamp(entry.get("submitted_at")))
        except ValueError:
            continue
    return times


def _match(message: Mapping[str, object], records: Sequence[Mapping[str, object]]) -> tuple[list[Mapping[str, object]], str]:
    listing_id = _text(message.get("platform_job_id")).strip()
    if listing_id:
        candidates = [
            record for record in records
            if listing_id in {
                _text(record.get("platform_job_id")).strip(),
                *(_text(v).strip() for v in (record.get("platform_job_ids") or []) if isinstance(v, str)),
            }
        ]
        mode = "platform_job_id"
    else:
        company, role = _normalized(message.get("company")), _normalized(message.get("role"))
        if not company or not role:
            return [], "missing_company_or_role"
        candidates = [
            record for record in records
            if _normalized(record.get("company")) == company
            and _normalized(record.get("role")) == role
        ]
        mode = "company_role"
    if len(candidates) > 1:
        return candidates, "ambiguous_application_match"
    if not candidates:
        return [], "no_application_match"
    record = candidates[0]
    if listing_id and any(
        _normalized(message.get(key))
        and _normalized(message.get(key)) != _normalized(record.get(key))
        for key in ("company", "role")
    ):
        return candidates, "contradictory_application_identity"
    confirmations = confirmed_submission_times(record)
    if not confirmations:
        return candidates, "application_not_confirmed"
    if _timestamp(message.get("received_at")) < min(confirmations):
        return candidates, "message_predates_confirmation"
    return candidates, mode


def plan_sync(
    records: Sequence[Mapping[str, object]],
    messages: Sequence[Mapping[str, object]],
    state: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Plan outcome proposals and review items without mutating inputs.

    State keys are ``processed_message_ids``, ``attention_queue`` and ``cursor``.
    Only safely classified messages with one confirmed application (and unrelated
    messages) enter the processed ledger. Queued messages remain eligible for a
    later retry. Full queued messages are retained privately for cursor-safe replay.
    """
    state = state or {}
    prior_ledger = state.get("processed_message_ledger", state.get("processed_message_ids", []))
    processed = list(dict.fromkeys(str(value) for value in prior_ledger if value))
    processed_set = set(processed)
    attention = [
        dict(item) for item in state.get("attention_queue", [])
        if isinstance(item, Mapping) and item.get("message_id")
    ]
    attention_by_id = {str(item["message_id"]): item for item in attention
                       if item["message_id"] not in processed_set}
    proposals: list[dict[str, object]] = []
    cursor_value = _text(state.get("cursor")) or None
    cursor_dt = _timestamp(cursor_value) if cursor_value else None

    seen_this_call: set[str] = set()
    retained = [item["message"] for item in attention
                if isinstance(item.get("message"), Mapping)]
    # Fresh adapter reads override retained copies without losing older queue IDs.
    combined = {item.get("message_id"): item for item in [*retained, *messages]}
    for message in combined.values():
        message_id = _text(message.get("message_id"))
        if not message_id or message_id != message_id.strip():
            raise ValueError("each message requires message_id")
        if message_id in seen_this_call:
            continue
        seen_this_call.add(message_id)
        received_at = _text(message.get("received_at"))
        received_dt = _timestamp(received_at)
        if cursor_dt is None or received_dt > cursor_dt:
            cursor_dt, cursor_value = received_dt, received_at
        if message_id in processed_set:
            continue

        classification = classify_message(message)
        if classification["needs_review"]:
            attention_by_id[message_id] = {
                "message_id": message_id,
                "received_at": received_at,
                "message": dict(message),
                "reason": classification["reason"],
                "classification": classification,
            }
            continue
        category = str(classification["category"])
        if category == "unrelated":
            processed.append(message_id)
            processed_set.add(message_id)
            attention_by_id.pop(message_id, None)
            continue

        candidates, match_reason = _match(message, records)
        if match_reason not in {"platform_job_id", "company_role"}:
            attention_by_id[message_id] = {
                "message_id": message_id,
                "received_at": received_at,
                "message": dict(message),
                "reason": match_reason,
                "candidate_application_ids": [
                    _record_id(record) for record in candidates if _record_id(record)
                ],
                "category": category,
            }
            continue

        record = candidates[0]
        proposals.append({
            "application_id": _record_id(record),
            "status": category,
            "message_id": message_id,
            "occurred_at": received_at,
            "matched_by": match_reason,
            "matched_cue": classification["matched_cues"],
        })
        processed.append(message_id)
        processed_set.add(message_id)
        attention_by_id.pop(message_id, None)

    ordered_attention = sorted(
        attention_by_id.values(),
        key=lambda item: (_text(item.get("received_at")), _text(item.get("message_id"))),
    )
    return {
        "proposals": proposals,
        "attention_queue": ordered_attention,
        "processed_message_ledger": processed,
        "cursor": cursor_value,
    }


def sync_with_adapter(
    adapter: ReadOnlyMailAdapter | None,
    records: Sequence[Mapping[str, object]],
    state: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Fetch messages through a read-only adapter, or return a typed blocker."""
    if adapter is None:
        return {
            "status": "blocked",
            "blocker": {
                "type": "credential_required",
                "reason": "live_verification_requires_credentials",
            },
        }
    since = _text((state or {}).get("cursor")) or None
    pending_ids = [item["message_id"] for item in (state or {}).get("attention_queue", [])
                   if isinstance(item, Mapping) and item.get("message_id")]
    message_ids = list(dict.fromkeys([*adapter.search(since=since), *pending_ids]))
    # Pending IDs must be fetched even if they predate the cursor or are absent
    # from list metadata. A failed get aborts rather than silently losing them.
    list(adapter.list(message_ids))
    messages = []
    for message_id in message_ids:
        item = adapter.get(message_id)
        if item.get("message_id") != message_id:
            raise ValueError("adapter message_id mismatch")
        messages.append(item)
    result = plan_sync(records, messages, state)
    result["status"] = "planned"
    return result
