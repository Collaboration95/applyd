"""Draft-only outreach; all storage and credentials belong to the caller.

Hold the workspace writer lock across each public mutation. load_records must
read fresh durable records; persist_record must atomically and durably replace
one private record before returning. No workspace/profile/catalog is read here.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from contacts import CONTACT_SOURCES
from outcomes import OUTCOME_STATUSES, responded
from seen_index import identity_keys


DEFAULT_POLICY = {"enabled": False, "business_days": 10,
                  "daily_draft_cap": 3, "do_not_contact": []}
_SGT = timezone(timedelta(hours=8))
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_STATES = {"draft_uncertain", "draft_created", "note_created", "not_created"}


class OutreachError(ValueError):
    """Invalid caller input or corrupt durable state; no adapter action allowed."""

    exit_code = 2


class CredentialBlocker(Exception):
    """Adapters may raise this for unavailable draft credentials; never retry."""


class DraftAdapter(Protocol):
    def create_draft(self, *, to: str, subject: str, body: str,
                     reservation_id: str) -> str:
        """Create only a draft; return its actual nonempty provider draft ID.

        No sending, replying, messaging, invites, or automatic retries. The
        reservation ID is correlation metadata, not permission to retry.
        """
        ...


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (AttributeError, TypeError, ValueError) as exc:
        raise OutreachError("Expected an offset-bearing timestamp") from exc


def _stamp(value):
    return _time(value).astimezone(_SGT).isoformat()


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(
        ord(c) < 32 or ord(c) == 127 for c in value)


def validate_policy(policy=None):
    """Validate a strict policy and fill disabled defaults."""
    if policy is None:
        policy = {}
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise OutreachError("Invalid outreach policy fields")
    result = {**deepcopy(DEFAULT_POLICY), **deepcopy(policy)}
    if type(result["enabled"]) is not bool:
        raise OutreachError("outreach.enabled must be boolean")
    for field in ("business_days", "daily_draft_cap"):
        if type(result[field]) is not int or result[field] < 0:
            raise OutreachError(f"outreach.{field} must be a non-negative integer")
    if not isinstance(result["do_not_contact"], list) or not all(_text(x) for x in result["do_not_contact"]):
        raise OutreachError("outreach.do_not_contact must be an array of nonempty strings")
    return result


def business_day_due(submitted_at, days, timezone_name="Asia/Singapore"):
    """Advance N weekdays at the submission's local time; no holiday calendar."""
    if type(days) is not int or days < 0:
        raise OutreachError("business days must be a non-negative integer")
    due = _time(submitted_at).astimezone(ZoneInfo(timezone_name))
    # Full weeks avoid an unbounded day-by-day loop for configured large waits.
    if days and due.weekday() >= 5:
        due += timedelta(days=7 - due.weekday())
        days -= 1
    weeks, remaining = divmod(days, 5)
    due += timedelta(days=weeks * 7)
    while remaining:
        due += timedelta(days=1)
        if due.weekday() < 5:
            remaining -= 1
    return due


def _canonical(url):
    if not _text(url) or any(c.isspace() for c in url) or "\\" in url:
        raise OutreachError("Invalid saved URL")
    try:
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            raise ValueError
        p.port
    except ValueError as exc:
        raise OutreachError("Invalid saved URL") from exc
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    if p.hostname.lower() == "linkedin.com" or p.hostname.lower().endswith(".linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", p.path)
        job_id = match[1] if match else query.get("currentJobId")
        if job_id and job_id.isdigit():
            return "https://www.linkedin.com/jobs/view/" + job_id
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_")
             and k.lower() not in {"trk", "trackingid", "refid", "position", "pagenum"}}
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
                       urlencode(sorted(query.items())), ""))


def _keys(record):
    keys = {"app:" + record["application_id"]}
    # Reuse the workspace's platform/requisition/cross-post identity rules.
    if not all(_text(record.get(k)) for k in ("company", "role", "location")):
        raise OutreachError("Application identity requires company, role and location")
    # The first key for an empty canonical is its URL hash; omit it. Keep
    # non-URL identities even on legacy records without listing URLs.
    keys.update(identity_keys(record, "")[1:])
    for field in ("canonical_listing_url", "listing_url"):
        if record.get(field):
            canonical = _canonical(record[field])
            keys.update(identity_keys(record, canonical))
    if record.get("outreach"):
        for event in record["outreach"]:
            keys.update(event["identity_keys"])
    return keys


def _snapshot(load_records, application_id, aliases):
    records = deepcopy(list(load_records()))
    if any(not isinstance(r, dict) or not _text(r.get("application_id")) for r in records):
        raise OutreachError("Invalid application records")
    if len({r["application_id"] for r in records}) != len(records):
        raise OutreachError("Duplicate application IDs in record snapshot")
    for record in records:
        events = record.get("outreach", [])
        if not isinstance(events, list):
            raise OutreachError("Invalid outreach history")
        for event in events:
            if (not isinstance(event, dict) or event.get("status") not in _STATES
                    or event.get("channel") not in {"email", "linkedin"}
                    or not _text(event.get("follow_up_id"))
                    or not isinstance(event.get("identity_keys"), list)
                    or not event["identity_keys"]
                    or not all(_text(k) for k in event["identity_keys"])):
                raise OutreachError("Invalid outreach reservation")
            _time(event.get("reserved_at"))
            if event["status"] == "draft_created" and not _text(event.get("adapter_id")):
                raise OutreachError("Draft history lacks an actual adapter ID")
            if event["status"] in {"draft_created", "note_created"} and event["status"] != {
                    "email": "draft_created", "linkedin": "note_created"}[event["channel"]]:
                raise OutreachError("Draft history channel mismatch")
            if event["status"] == "not_created":
                reconciliation = event.get("reconciliation", {})
                if (reconciliation.get("resolution") != "not_created" or event.get("manual_sends")
                        or not _text(reconciliation.get("evidence")) or not _text(reconciliation.get("note"))):
                    raise OutreachError("Released reservation lacks explicit reconciliation")
    by_id = {r["application_id"]: r for r in records}
    if application_id not in by_id:
        raise OutreachError("Unknown application ID")
    keys = {rid: _keys(r) for rid, r in by_id.items()}
    # Each caller-supplied alias set joins known records; persist the union in
    # the reservation so later identity edits cannot erase its original scope.
    for group in aliases:
        if not isinstance(group, (list, tuple)) or not group or any(rid not in by_id for rid in group):
            raise OutreachError("Aliases must reference existing application IDs")
        token = "alias:" + hashlib.sha256("\0".join(sorted(group)).encode()).hexdigest()
        for rid in group:
            keys[rid].add(token)
    connected, union = {application_id}, set(keys[application_id])
    while True:
        added = {rid for rid in by_id if rid not in connected and union.intersection(keys[rid])}
        if not added:
            break
        connected.update(added)
        for rid in added:
            union.update(keys[rid])
    return records, by_id[application_id], [by_id[rid] for rid in sorted(connected)], sorted(union)


def _count(records, now, timezone_name):
    day = _time(now).astimezone(ZoneInfo(timezone_name)).date()
    return len({e["follow_up_id"] for r in records for e in r.get("outreach", [])
                if e["status"] != "not_created"
                and (e["status"] == "draft_uncertain"
                     or _time(e["reserved_at"]).astimezone(ZoneInfo(timezone_name)).date() == day)})


def _result(record, status, reason, count, event=None):
    result = {"application_id": record["application_id"], "status": status,
              "reason": reason, "daily_count": count}
    if event:
        result["follow_up_id"] = event["follow_up_id"]
    return result


def _submission(record, point):
    """Find evidence-backed submitted selection; never use prepared/current CV."""
    if not isinstance(point, dict) or set(point) != {"project_id", "bullet_id", "text", "catalog_fingerprint"}:
        return None
    if not all(_text(v) for v in point.values()) or not _HASH.fullmatch(point["catalog_fingerprint"]):
        return None
    submissions = record.get("submissions", [])
    if not isinstance(submissions, list):
        return None
    for saved in submissions:
        if not isinstance(saved, dict) or saved.get("status", "submitted") != "submitted":
            continue
        if (not saved.get("submitted_at") or not _text(saved.get("confirmation_path"))
                or not _HASH.fullmatch(str(saved.get("confirmation_sha256", "")))
                or not _text(saved.get("id")) or not _text(saved.get("exact_resume_copy"))
                or not _HASH.fullmatch(str(saved.get("sha256", "")))):
            continue
        _time(saved["submitted_at"])
        selection = saved.get("selected_projects", [])
        if not isinstance(selection, list):
            continue
        for selected in selection:
            if (isinstance(selected, dict) and selected.get("id") == point["project_id"]
                    and selected.get("content_hash") == point["catalog_fingerprint"]
                    and isinstance(selected.get("bullet_ids"), list)
                    and point["bullet_id"] in selected.get("bullet_ids", [])):
                return saved
    return None


def create_follow_up(application_id, *, load_records, persist_record, policy=None,
                     now, contact_id, channel, trusted_point, adapter=None,
                     timezone_name="Asia/Singapore", aliases=(), run_id=None):
    """Reserve then create one draft/local note under the caller's writer lock.

    trusted_point must be supplied by the parent's checked catalog/CV source
    hook, not user/job/free-form model text. See docs/outreach.md for its schema.
    Adapter exceptions and missing actual IDs leave the reservation uncertain.
    """
    policy = validate_policy(policy)
    if run_id is not None and not _text(run_id):
        raise OutreachError("Invalid outreach run ID")
    now = _time(now)
    records, record, group, keys = _snapshot(load_records, application_id, aliases)
    count = _count(records, now, timezone_name)
    blocked = lambda reason, event=None: _result(record, "blocked", reason, count, event)
    if not policy["enabled"]:
        return blocked("disabled")
    if channel not in {"email", "linkedin"}:
        raise OutreachError("channel must be email or linkedin")
    active = [e for r in group for e in r.get("outreach", []) if e["status"] != "not_created"]
    if active:
        return blocked("draft_uncertain" if active[0]["status"] == "draft_uncertain" else "already_followed_up", active[0])
    if any(responded(r) or r.get("outcome") or r.get("outcome_events")
           or any(e.get("status") in OUTCOME_STATUSES for e in r.get("events", []))
           or r.get("status") in OUTCOME_STATUSES for r in group):
        return blocked("response_or_closed")
    if any(r.get("status") == "submission_uncertain" or r.get("submission_intent") for r in group):
        return blocked("submission_uncertain")
    contact = next((c for c in record.get("contacts", []) if c.get("contact_id") == contact_id), None)
    if (not contact or contact.get("source") not in CONTACT_SOURCES
            or not _text(contact.get("captured_at")) or contact.get("verified") is not False):
        return blocked("contact_missing")
    _time(contact["captured_at"])
    tokens = {str(v).strip().casefold() for r in group
              for v in (r.get("application_id"), r.get("company"), r.get("listing_url"), r.get("canonical_listing_url")) if v}
    tokens.update(str(contact[k]).strip().casefold() for k in ("contact_id", "name", "email", "profile_url") if contact.get(k))
    if (any(r.get("do_not_contact") is True for r in group) or contact.get("do_not_contact") is True
            or tokens.intersection(x.strip().casefold() for x in policy["do_not_contact"])):
        return blocked("do_not_contact")
    if count >= policy["daily_draft_cap"]:
        return blocked("daily_cap")
    saved = _submission(record, trusted_point)
    if saved is None:
        return blocked("unverifiable_project_source")
    if now < business_day_due(saved["submitted_at"], policy["business_days"], timezone_name):
        return blocked("not_due")
    role = record.get("role")
    if not _text(role):
        return blocked("role_missing")
    text = f"Following up on {role}. {trusted_point['text']} Thanks!"
    subject = f"Follow-up: {role}"
    if channel == "linkedin":
        profile = contact.get("profile_url")
        if not profile:
            return blocked("profile_missing")
        _canonical(profile)
        host = urlsplit(profile).hostname.lower()
        if (host != "linkedin.com" and not host.endswith(".linkedin.com")) or not re.fullmatch(
                r"/(?:in|pub)/[^/]+(?:/[^/]+)*/?", urlsplit(profile).path):
            return blocked("profile_missing")
        if len(text.encode("utf-16-le")) // 2 > 200:
            return blocked("note_too_long")  # Never truncate/invent a project claim.
    else:
        email = contact.get("email")
        if not _text(email) or not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", email):
            return blocked("email_missing")
        if adapter is None or not callable(getattr(adapter, "create_draft", None)):
            record["outreach_blocker"] = {"reason": "credentials_required", "recorded_at": _stamp(now)}
            persist_record(deepcopy(record))
            return blocked("credentials_required")
    # Reusing a reconciled not-created slot uses a NEW ID; old audit survives.
    attempt = sum(len(r.get("outreach", [])) for r in group)
    follow_up_id = "follow-up-" + hashlib.sha256(
        ("\0".join(keys) + f"\0{attempt}").encode()).hexdigest()
    event = {"follow_up_id": follow_up_id, "status": "draft_uncertain",
             "reserved_at": _stamp(now), "channel": channel, "identity_keys": keys,
             "contact_id": contact_id, "text": text, "subject": subject,
             "sources": {"application_id": application_id, "role": role,
                         "listing_url": record.get("listing_url"), "contact": deepcopy(contact),
                         "submitted_at": saved["submitted_at"],
                         "confirmation_path": saved["confirmation_path"],
                         "confirmation_sha256": saved["confirmation_sha256"],
                         "resume_id": saved["id"], "resume_sha256": saved["sha256"],
                         "exact_resume_copy": saved["exact_resume_copy"],
                         "source_master_id": saved.get("source_master_id"),
                         "project": deepcopy(trusted_point)},
             "policy": deepcopy(policy), "timezone": timezone_name}
    if channel == "linkedin":
        event["profile_url"] = profile
    if run_id is not None:
        event["run_id"] = run_id
    record.setdefault("outreach", []).append(event)
    record.pop("outreach_blocker", None)
    # This call must finish a durable write before any external draft creation.
    persist_record(deepcopy(record))
    count += 1
    if channel == "email":
        try:
            actual_id = adapter.create_draft(to=email, subject=subject, body=text,
                                             reservation_id=follow_up_id)
        except CredentialBlocker:
            event["blocker"] = "credentials_required"
        except Exception:
            event["blocker"] = "adapter_error"
        else:
            if _text(actual_id):
                event.update(status="draft_created", adapter_id=actual_id)
            else:
                event["blocker"] = "adapter_id_missing"
    else:
        event["status"] = "note_created"
    if event["status"] != "draft_uncertain":
        event["created_at"] = _stamp(now)
    persist_record(deepcopy(record))
    return _result(record, event["status"], event.get("blocker", "created"), count, event)


def reconcile_follow_up(application_id, follow_up_id, *, load_records, persist_record,
                        now, resolution, evidence, note, adapter_id=None):
    """Explicit human reconciliation only; never invoke an adapter here."""
    _, record, _, _ = _snapshot(load_records, application_id, ())
    event = _find(record, follow_up_id)
    if resolution not in {"draft_created", "note_created", "not_created"} or not _text(evidence) or not _text(note):
        raise OutreachError("Reconciliation requires a resolution, evidence reference and note")
    if resolution != "not_created" and resolution != {"email": "draft_created", "linkedin": "note_created"}[event["channel"]]:
        raise OutreachError("Resolution does not match the channel")
    if resolution == "draft_created" and not _text(adapter_id):
        raise OutreachError("Actual adapter draft ID is required")
    change = {"resolution": resolution, "evidence": evidence, "note": note, "adapter_id": adapter_id}
    if event.get("reconciliation") and all(event["reconciliation"].get(k) == v for k, v in change.items()):
        return {"application_id": application_id, "follow_up_id": follow_up_id, "status": event["status"]}
    if event["status"] != "draft_uncertain" or (resolution == "not_created" and event.get("manual_sends")):
        raise OutreachError("Only uncertainty may be reconciled; a confirmed send cannot be released")
    event["reconciliation"] = {**change, "recorded_at": _stamp(now)}
    event["status"] = resolution
    if resolution == "draft_created":
        event["adapter_id"] = adapter_id
    event.pop("blocker", None)
    persist_record(record)
    return {"application_id": application_id, "follow_up_id": follow_up_id, "status": resolution}


def _find(record, follow_up_id):
    for event in record.get("outreach", []):
        if event["follow_up_id"] == follow_up_id:
            return event
    raise OutreachError("Unknown follow-up ID")


def confirm_manual_send(application_id, follow_up_id, *, load_records, persist_record,
                        occurred_at, recorded_at, evidence):
    """Log the user's confirmed manual send privately; perform no external action."""
    _, record, _, _ = _snapshot(load_records, application_id, ())
    event = _find(record, follow_up_id)
    if event["status"] == "not_created" or not _text(evidence):
        raise OutreachError("Manual send requires a reserved follow-up and evidence")
    occurred, recorded = _time(occurred_at), _time(recorded_at)
    if occurred < _time(event["reserved_at"]) or occurred > recorded:
        raise OutreachError("Manual send timestamps are out of order")
    sent = {"occurred_at": _stamp(occurred), "evidence": evidence}
    previous = event.get("manual_sends", [])
    if previous and any(previous[0].get(k) != v for k, v in sent.items()):
        raise OutreachError("A manual send is already recorded")
    if not previous:
        event["manual_sends"] = [{**sent, "recorded_at": _stamp(recorded)}]
        persist_record(record)
    return {"application_id": application_id, "follow_up_id": follow_up_id, "status": "manual_send_recorded"}
