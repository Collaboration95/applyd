"""Build private dashboard control-plane fields from one record snapshot."""

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from answers import QUESTION_KEYS, _rows, bank_version
from settings import load_settings, settings_hash
from outcomes import overdue, responded


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _record_url(root, application_id):
    if not isinstance(application_id, str) or not application_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in application_id):
        return None
    path = root / "applications" / "records" / application_id / "record.json"
    return f"../applications/records/{application_id}/record.json" if path.is_file() else None


def _attention(record, now):
    status = record.get("status")
    blocked = record.get("blocked_on_answer_ids") or []
    updated = record.get("status_updated_at") or (record.get("prepared") or {}).get("prepared_at")
    stale = False
    if status in {"preparing", "ready"} and updated:
        try:
            at = datetime.fromisoformat(updated)
            stale = at.tzinfo is not None and (now - at).total_seconds() >= 86400
        except (TypeError, ValueError):
            stale = False
    if status in {"submitted", "interview", "rejected", "withdrawn", "skipped", "saved", "dry_run_complete", "acknowledged", "assessment", "offer"} and not blocked:
        return None
    reason = record.get("stop_reason") or record.get("last_error") or record.get("reason")
    if not reason:
        if record.get("answers_resolved"):
            reason = "Answers resolved; execution checks pending."
        elif status in {"awaiting_answer", "blocked"} or blocked:
            reason = f"Waiting for {len(blocked)} answer{'s' if len(blocked) != 1 else ''}." if blocked else "Waiting for an answer."
        elif status == "submission_uncertain":
            reason = "Submission needs reconciliation before retry."
        elif status == "failed":
            reason = "Application attempt failed."
        elif stale:
            reason = f"No progress recorded for at least 24 hours in {status}."
        elif status == "discovered":
            reason = "Queued for preparation."
        elif status == "ready":
            reason = "Ready; queued for submission checks."
        else:
            reason = "Queued; preparation in progress."
    return {"application_id": record.get("application_id"), "title": record.get("role"),
            "company": record.get("company"), "status": status, "reason": str(reason), "stale": stale,
            "stop_reason": record.get("stop_reason"), "listing_url": record.get("canonical_listing_url") or record.get("listing_url"),
            "record_url": None}


def _current_inbox(bank, inbox, records, now):
    """Resolve saved wording against the current bank, keeping inbox identities.

    The core resolver is pure: unlike prepare_answers/context it writes nothing.
    Missing records cannot establish application scope and stay unresolved.
    """
    result = copy.deepcopy(inbox)
    by_id = {r.get("application_id"): r for r in records if isinstance(r, dict)}
    source_fields = {"source_answer_id", "source_version", "source_fingerprint",
                     "source_value", "source_scope", "basis", "source", "confirmed_at"}
    for group in result.get("questions", []):
        occurrences = []
        for original in group.get("occurrences", []):
            record = by_id.get(original.get("application_id"))
            occurrence = {k: v for k, v in original.items() if k not in source_fields}
            if record:
                question = {k: v for k, v in original.items() if k in QUESTION_KEYS}
                row = _rows(bank, record, [question], now)[0]
                occurrence.update(row)
                occurrence["resolved_question_id"] = row["question_id"]
                occurrence["question_id"] = group["question_id"]
                occurrence["occurrence_id"] = original["occurrence_id"]
            else:
                occurrence.update(answer=None, resolution="application_context_unavailable")
            occurrences.append(occurrence)
        group["occurrences"] = occurrences
        group["status"] = "resolved" if occurrences and all(
            o.get("resolution") == "resolved" and o.get("answer") is not None
            for o in occurrences) else "unresolved"
    return result


def enrich_payload(root, records_snapshot, payload, now=None):
    """Add settings, answer provenance, inbox, outcomes and attention without writes.

    ``records_snapshot`` is the single authoritative record scan already used by
    the parent exporter. This function does not modify it or rescan records.
    """
    root = Path(root).resolve()
    settings = load_settings(root)
    timezone = ZoneInfo(settings["timezone"])
    if now is None:
        now = datetime.now(timezone)
    elif isinstance(now, str):
        now = datetime.fromisoformat(now)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime or ISO timestamp")
    now = now.astimezone(timezone)
    # One authoritative bank read per export. No context() call, because that API writes.
    bank = _read_json(root / "profile" / "answers.json", {"schema_version": 1, "answers": []})
    if not isinstance(bank, dict):
        raise ValueError("profile/answers.json must contain an object")
    inbox = _read_json(root / "profile" / "question-inbox.json", {"schema_version": 1, "questions": []})
    if not isinstance(inbox, dict) or not isinstance(inbox.get("questions", []), list):
        raise ValueError("profile/question-inbox.json has an invalid schema")

    records = records_snapshot.values() if isinstance(records_snapshot, dict) else records_snapshot
    records = list(records or [])
    inbox = _current_inbox(bank, inbox, records, now)
    payload = copy.deepcopy(payload)
    apps_by_id = {a.get("id"): a for a in payload.get("applications", []) if isinstance(a, dict)}
    attention = []
    outcomes = []
    answer_unlock_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        app_id = record.get("application_id")
        record = copy.deepcopy(record)
        occurrences = [o for group in inbox.get("questions", []) for o in group.get("occurrences", []) if o.get("application_id") == app_id]
        if occurrences:
            known = {o["question_id"] for o in occurrences} | {o.get("resolved_question_id") for o in occurrences}
            missing = set(record.get("blocked_on_answer_ids") or []) - known
            blocked = {o["question_id"] for o in occurrences if o.get("answer") is None or o.get("resolution") != "resolved"} | missing
            record["blocked_on_answer_ids"] = sorted(blocked)
            record["answers_resolved"] = not blocked
        app = apps_by_id.get(app_id)
        if app:
            app["blocked_on_answer_ids"] = list(record.get("blocked_on_answer_ids") or [])
            app["stop_reason"] = record.get("stop_reason")
            app["record_url"] = _record_url(root, app_id)
            app["responded"] = responded(record)
        outcome = record.get("outcome") or record.get("response")
        if not outcome and record.get("status") in {"interview", "rejected"}:
            outcome = record["status"]
        if outcome:
            outcomes.append({"application_id": app_id, "outcome": outcome,
                             "at": record.get("latest_outcome_at") or record.get("outcome_at") or record.get("status_updated_at")})
            if app:
                app["response"] = outcome
        item = _attention(record, now)
        if (record.get("submissions") or record.get("submitted_at")) and overdue(record, now.isoformat(), settings["follow_up_days"]):
            item = item or {"application_id": app_id, "company": record.get("company"), "title": record.get("role"),
                            "status": record.get("status"), "listing_url": record.get("canonical_listing_url") or record.get("listing_url")}
            item.update(reason="Follow-up is overdue.", stop_reason="follow_up_overdue")
        if item:
            item["record_url"] = _record_url(root, app_id)
            attention.append(item)
        if (record.get("status") in {"awaiting_answer", "preparing", "ready"}
                and (record.get("answers_resolved") or record.get("execution_checks_required") is True)
                and not (record.get("blocked_on_answer_ids") or [])):
            answer_unlock_count += 1

    receipts = []
    imports = bank.get("imports", {})
    if isinstance(imports, dict):
        for import_id, receipt in sorted(imports.items()):
            result = receipt.get("result", {}) if isinstance(receipt, dict) else {}
            receipts.append({"import_id": import_id, "bank_version": result.get("bank_version"),
                             "confirmed_at": receipt.get("confirmed_at") if isinstance(receipt, dict) else None})
    payload.update({
        "timezone": settings["timezone"],
        "settings": settings,
        "settings_hash": settings_hash(settings),
        "bank_version": bank_version(bank),
        "answer_sources": bank.get("answers", []),
        "answer_receipts": receipts,
        "inbox": inbox,
        "outcomes": outcomes,
        "attention": attention,
        "answer_unlock_count": answer_unlock_count,
    })
    return payload
