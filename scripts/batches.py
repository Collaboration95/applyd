"""Pure batch accounting over caller-supplied application records."""

from copy import deepcopy
from datetime import datetime

import seen_index
import settings as settings_core


def validate_event_time(value, field="occurred_at"):
    """Return an aware datetime from an ISO string or datetime; never add a zone."""
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{field} requires an ISO timestamp with a UTC offset") from exc
    return parsed


def event_metadata(occurred_at, recorded_at):
    """Build event times; legacy `at` remains the actual occurrence time."""
    occurred = validate_event_time(occurred_at)
    recorded = validate_event_time(recorded_at, "recorded_at")
    if occurred > recorded:
        raise ValueError("occurred_at must not be later than recorded_at")
    return {"at": occurred.isoformat(), "occurred_at": occurred.isoformat(),
            "recorded_at": recorded.isoformat()}


def _label(value, field, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")


def attach_attempt_context(snapshot, batch_id, category=None):
    """Return a deep copy with frozen context; reject changing an existing value."""
    if not isinstance(snapshot, dict):
        raise ValueError("Prepared snapshot must be an object")
    _label(batch_id, "batch_id", optional=True)
    _label(category, "category", optional=True)
    if category is not None and batch_id is None:
        raise ValueError("category requires batch_id")
    for field, value in (("batch_id", batch_id), ("category", category)):
        if field in snapshot and snapshot[field] != value:
            raise ValueError(f"Prepared {field} is frozen; prepare a new attempt")
    if snapshot.get("prepared_at") is not None:
        validate_event_time(snapshot["prepared_at"], "prepared_at")
    return {**deepcopy(snapshot), "batch_id": batch_id, "category": category}


def _keys(record):
    # Lazy import: the CLI imports this module, and owns the canonical URL policy.
    from workspace import canonical_url

    normalized = dict(record)
    for field in ("canonical_listing_url", "listing_url"):
        if record.get(field):
            normalized[field] = canonical_url(record[field])
    if record.get("employer_requisition_id"):
        normalized["employer_requisition_id"] = seen_index.normal(record["employer_requisition_id"])
    keys = set(settings_core._identity_keys(normalized))
    # canonical_url already resolves LinkedIn URL variants. Link that canonical
    # identity to a sparse platform/job-ID record without requiring a full header.
    prefix = "https://www.linkedin.com/jobs/view/"
    for field in ("canonical_listing_url", "listing_url"):
        canonical = normalized.get(field, "") or ""
        if canonical.startswith(prefix):
            keys.update(settings_core._identity_keys(
                {"platform": "linkedin", "platform_job_id": canonical[len(prefix):]}))
    return keys


def _groups(records):
    """Connect every shared identity, including unconfirmed cross-post bridges."""
    records = list(records)
    parents, identities = list(range(len(records))), {}

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("Application records must be objects")
        _label(record.get("application_id"), "application_id")
        for key in _keys(record):
            if key in identities:
                parents[find(i)] = find(identities[key])
            identities[key] = i
    groups = {}
    for i, record in enumerate(records):
        groups.setdefault(find(i), []).append(record)
    return list(groups.values())


def _confirmations(record):
    saved = record.get("submissions", [])
    if not isinstance(saved, list) or any(not isinstance(s, dict) for s in saved):
        raise ValueError("submissions must be an array of objects")
    result = []
    for submission in saved:
        for field in ("submitted_at", "occurred_at", "recorded_at", "at"):
            if submission.get(field) is not None:
                validate_event_time(submission[field], field)
        if submission.get("status", "submitted") != "submitted" or not submission.get("submitted_at"):
            continue
        if not (submission.get("confirmation_path") or submission.get("evidence")):
            continue
        at = validate_event_time(submission.get("occurred_at") or submission["submitted_at"])
        if submission.get("recorded_at"):
            event_metadata(at, submission["recorded_at"])
        _label(submission.get("batch_id"), "batch_id", optional=True)
        _label(submission.get("category"), "category", optional=True)
        result.append((at, submission.get("batch_id"), submission.get("category")))
    # Legacy confirmations can exclude a job, but mutable top-level batch/category
    # fields cannot prove ownership. Keep even an older legacy time as a barrier.
    if record.get("submitted_at"):
        at = validate_event_time(record["submitted_at"], "submitted_at")
        if (record.get("confirmation_path") or record.get("evidence")) and not any(t == at for t, _, _ in result):
            result.append((at, None, None))
    return result


def _state(group):
    confirmations = [event for record in group for event in _confirmations(record)]
    uncertain = any(r.get("status") == "submission_uncertain" or r.get("submission_intent") for r in group)
    return confirmations, uncertain


def batch_progress(records, batch_id, target, category_targets=None):
    """Count each job's first evidence-backed confirmation, using frozen context."""
    _label(batch_id, "batch_id")
    category_targets = {} if category_targets is None else category_targets
    if not isinstance(category_targets, dict):
        raise ValueError("category_targets must be an object")
    for category, quota in category_targets.items():
        _label(category, "category")
        if type(quota) is not int or quota < 0:
            raise ValueError("Category targets must be non-negative integers")
    if type(target) is not int or target < 0:
        raise ValueError("target must be a non-negative integer")
    counts = dict.fromkeys(category_targets, 0)
    counted, excluded, uncertain = [], [], []
    for group in _groups(records):
        confirmations, pending = _state(group)
        ids = sorted({r["application_id"] for r in group})
        if confirmations:
            excluded.extend(ids)
            first = min(at for at, _, _ in confirmations)
            owners = {(batch, category) for at, batch, category in confirmations if at == first}
            # Conflicting/unknown original ownership never earns a new quota.
            if {batch for batch, _ in owners} == {batch_id}:
                counted.append(ids[0])
                categories = {category for _, category in owners}
                if len(categories) == 1:
                    category = next(iter(categories))
                    if category in counts:
                        counts[category] += 1
        elif pending:
            uncertain.extend(ids)
    categories = {name: {"target": quota, "confirmed": counts[name],
                         "remaining": max(0, quota - counts[name]), "complete": counts[name] >= quota}
                  for name, quota in category_targets.items()}
    return {"batch_id": batch_id, "target": target, "confirmed": len(counted),
            "remaining": max(0, target - len(counted)),
            "complete": len(counted) >= target and all(c["complete"] for c in categories.values()),
            "categories": categories, "confirmed_application_ids": sorted(counted),
            "exclusions": {"confirmed": sorted(set(excluded)),
                           "submission_uncertain": sorted(set(uncertain))}}


def retryable_application_ids(records, settings):
    """Propose resolved retries; execution still requires the parent's live gates."""
    statuses = settings.get("retryable_statuses", settings_core.DEFAULTS["retryable_statuses"])
    if not isinstance(statuses, list) or any(not isinstance(s, str) for s in statuses):
        raise ValueError("retryable_statuses must be an array of strings")
    result = []
    for group in _groups(records):
        confirmed, uncertain = _state(group)
        if confirmed or uncertain:
            continue
        for record in group:
            status = record.get("status")
            if (status in statuses and status in settings_core._RETRYABLE
                    and not record.get("blocked_on_answer_ids")):
                result.append(record["application_id"])
    return sorted(set(result))
