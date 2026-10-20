"""Crash-safe local email import. Call sync_local only under the writer lock.

No mailbox client, credentials, network, calendar, or outbound operations live
here. Inputs and evidence stay inside the selected private workspace.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from gmail_sync import _timestamp, plan_sync
from outcomes import apply_outcome
from settings import load_settings


STATE_PATH = Path("applications/mail/state.json")
CONTACTS_PATH = Path("applications/mail/contacts.json")
MESSAGES_PATH = Path("applications/mail/messages")


class MailImportError(ValueError):
    """An import refusal whose message contains no private input or paths."""


def _path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise MailImportError("mail_artifact_path_invalid")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise MailImportError("mail_artifact_symlink_refused")
    return path


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MailImportError("mail_json_read_failed") from exc


def _bytes(value):
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MailImportError("mail_json_invalid") from exc


def _flush_directory(path):
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_json(path, value):
    """Flush directory creation, file data, and the atomic rename in order."""
    data = _bytes(value)
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _flush_directory(directory.parent)
    fd, temporary = tempfile.mkstemp(prefix=".mail-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _flush_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_messages(messages):
    if not isinstance(messages, (list, tuple)):
        raise MailImportError("mail_export_requires_messages_array")
    unique = {}
    for message in messages:
        if not isinstance(message, dict):
            raise MailImportError("mail_message_invalid")
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not message_id or message_id != message_id.strip():
            raise MailImportError("mail_message_id_required")
        try:
            _timestamp(message.get("received_at"))
        except ValueError as exc:
            raise MailImportError("mail_received_at_invalid") from exc
        for field in ("subject", "body", "thread_id", "company", "role", "platform_job_id"):
            if field in message and not isinstance(message[field], str):
                raise MailImportError("mail_message_field_invalid")
        _bytes(message)
        if message_id in unique and unique[message_id] != message:
            raise MailImportError("mail_message_id_collision")
        unique[message_id] = deepcopy(message)
    return unique


def read_export(path):
    """Read a supplied local export: an array or {\"messages\": [...]} only."""
    data = _read(Path(path))
    messages = data.get("messages") if isinstance(data, dict) else data
    _validate_messages(messages)
    return messages


def _verified_record(root, record):
    """Pass only byte-verified confirmation metadata to the pure matcher."""
    result = deepcopy(record)
    for entry in [result, *(result.get("submissions") or [])]:
        if not isinstance(entry, dict):
            raise MailImportError("mail_application_record_invalid")
        relative, expected = entry.get("confirmation_path"), entry.get("confirmation_sha256")
        valid = False
        if isinstance(relative, str) and isinstance(expected, str) and relative and expected:
            try:
                path = _path(root, relative)
                valid = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected.lower()
            except (OSError, MailImportError):
                pass
        if not valid:
            entry.pop("confirmation_path", None)
            entry.pop("confirmation_sha256", None)
    return result


def _event_message_id(event):
    evidence = event.get("evidence")
    return evidence.get("message_id") if isinstance(evidence, dict) else None


def _load_records(root):
    directory = _path(root, "applications/records")
    loaded = {}
    for path in sorted(directory.glob("*/record.json")):
        _path(root, path.relative_to(root))
        record = _read(path)
        if (not isinstance(record, dict) or not isinstance(record.get("application_id"), str)
                or not record["application_id"] or record["application_id"] in loaded):
            raise MailImportError("mail_application_record_invalid")
        loaded[record["application_id"]] = (path, record)
    return loaded


def sync_local(root, messages, now):
    """Persist supplied messages and replay retained private evidence safely.

    Caller MUST hold workspace.writer(root) through this call and any derived
    export. ``messages=None`` returns a credential blocker without reading or
    writing the workspace. An empty array retries retained messages locally.
    Return values contain only status, counters, and fixed blocker codes.
    """
    if messages is None:
        return {"status": "blocked", "blocker": {
            "type": "credential_required", "reason": "live_verification_requires_credentials"}}
    try:
        return _sync_local(root, messages, now)
    except MailImportError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Filesystem and config errors can embed private names/paths. The CLI
        # must print only this code, never raw exception text or tracebacks.
        raise MailImportError("mail_import_failed") from None


def _sync_local(root, messages, now):
    try:
        recorded = _timestamp(now.isoformat() if isinstance(now, datetime) else now)
    except ValueError as exc:
        raise MailImportError("mail_now_requires_offset") from exc
    recorded_at = recorded.astimezone(ZoneInfo("Asia/Singapore")).isoformat()
    incoming = _validate_messages(messages)
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise MailImportError("mail_workspace_missing")

    state_path = _path(root, STATE_PATH)
    state = _read(state_path) if state_path.exists() else {}
    if not isinstance(state, dict):
        raise MailImportError("mail_state_invalid")
    contacts_path = _path(root, CONTACTS_PATH)
    contacts = _read(contacts_path) if contacts_path.exists() else []
    if not isinstance(contacts, list) or any(not isinstance(e, dict) for e in contacts):
        raise MailImportError("mail_contacts_invalid")
    loaded = _load_records(root)
    matching_records = [_verified_record(root, record) for _, record in loaded.values()]
    follow_up_days = load_settings(root)["follow_up_days"]

    # Every saved event is authoritative even if a crash prevented ledger flush.
    committed = {_event_message_id(e) for _, record in loaded.values()
                 for e in record.get("outcome_events", [])}
    committed.update(_event_message_id(e) for e in contacts)
    committed.discard(None)
    ledger = set(state.get("processed_message_ledger", []))

    # Scan the durable inbox, including messages staged before an interrupted
    # state write. Never rely on the cursor to recover queued/staged messages.
    archived = {}
    message_dir = _path(root, MESSAGES_PATH)
    for path in sorted(message_dir.glob("*.json")):
        _path(root, path.relative_to(root))
        saved = _validate_messages([_read(path)])
        message_id, message = next(iter(saved.items()))
        if path.name != hashlib.sha256(message_id.encode()).hexdigest() + ".json":
            raise MailImportError("mail_evidence_identity_invalid")
        archived[message_id] = message
    for message_id, message in incoming.items():
        if message_id in archived and archived[message_id] != message:
            raise MailImportError("mail_message_id_collision")
    # Mark derived views dirty before authoritative writes, including crashes.
    _atomic_json(_path(root, Path("applications/export-state.json")),
                 {"schema_version": 1, "dirty": True, "status": "pending"})
    # Stage ALL full messages before an outcome or cursor can be written. File
    # names are hashes; neither message IDs nor sender names become paths.
    for message_id, message in incoming.items():
        if message_id not in archived:
            name = hashlib.sha256(message_id.encode()).hexdigest() + ".json"
            _atomic_json(_path(root, MESSAGES_PATH / name), message)
            archived[message_id] = message

    planning_state = {**state, "processed_message_ledger": sorted(ledger | committed)}
    result = plan_sync(matching_records, list(archived.values()), planning_state)
    counts = {"received": len(messages), "outcomes": 0, "contacts": 0,
              "unrelated": 0, "duplicates": len(incoming.keys() & (ledger | committed)),
              "recovered": len(committed - ledger), "queued": len(result["attention_queue"])}
    for proposal in result["proposals"]:
        message_id = proposal["message_id"]
        evidence_file = _path(root, MESSAGES_PATH / (hashlib.sha256(message_id.encode()).hexdigest() + ".json"))
        evidence = {"message_id": message_id, "path": str(evidence_file.relative_to(root)),
                    "sha256": hashlib.sha256(evidence_file.read_bytes()).hexdigest()}
        if proposal["status"] == "recruiter_message":
            # A contact event does not create an application, submission,
            # outcome, public contact profile, or follow-up date.
            contacts.append({"type": "recruiter_message", "read_only": True,
                             "application_id": proposal["application_id"],
                             "occurred_at": proposal["occurred_at"], "recorded_at": recorded_at,
                             "evidence": evidence})
            _atomic_json(contacts_path, contacts)
            counts["contacts"] += 1
        else:
            path, record = loaded[proposal["application_id"]]
            updated = apply_outcome(record, proposal["status"], proposal["occurred_at"], recorded_at,
                                    evidence=evidence, follow_up_days=follow_up_days)
            # No subject, body, company, role or sender is copied to event data.
            updated.setdefault("events", []).append({
                "at": recorded_at, "recorded_at": recorded_at, "occurred_at": proposal["occurred_at"],
                "status": proposal["status"], "evidence": evidence})
            updated.update(status=updated["outcome"], status_updated_at=recorded_at)
            _atomic_json(path, updated)
            loaded[proposal["application_id"]] = (path, updated)
            counts["outcomes"] += 1
        committed.add(message_id)

    # Only successful event writes and unrelated messages enter the ledger.
    # If this write fails, the archived inbox and event IDs recover the run.
    completed = set(result["processed_message_ledger"])
    counts["unrelated"] = len(completed - ledger - committed)
    _atomic_json(state_path, {"schema_version": 1,
                             "processed_message_ledger": sorted(completed),
                             "attention_queue": result["attention_queue"], "cursor": result["cursor"],
                             "recorded_at": recorded_at})
    return {"status": "synced", "counts": counts}
