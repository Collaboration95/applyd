"""Checked workspace boundary for draft-only outreach (caller holds writer lock).

Import workspace lazily: workspace may import these command hooks itself.
No browser, credential acquisition, profile discovery or live email adapter.
"""

from datetime import timedelta, timezone
import hashlib
from pathlib import Path
import re

import outreach as core
import settings


def safe_evidence(root, reference, expected_sha256=None):
    """Return {path, sha256} for nonempty saved bytes inside the workspace.

    Relative paths and in-workspace absolute Paths are accepted. Traversal,
    symlink escapes, missing/empty files and mismatched expected hashes fail.
    This reads existing evidence; it never copies or manufactures evidence.
    """
    root = Path(root).resolve(strict=True)
    if not isinstance(reference, (str, Path)) or not str(reference).strip():
        raise core.OutreachError("Nonempty saved evidence is required")
    path = Path(reference)
    if ".." in path.parts:
        raise core.OutreachError("Evidence path must stay inside the workspace")
    try:
        path = (root / path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise core.OutreachError("Evidence path must be a file inside the workspace")
        content = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        raise core.OutreachError("Saved evidence is missing or unreadable") from exc
    if not content.strip():
        raise core.OutreachError("Nonempty saved evidence is required")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or expected_sha256 != digest):
        raise core.OutreachError("Saved evidence hash mismatch")
    return {"path": path.relative_to(root).as_posix(), "sha256": digest}


def effective_policy(current, frozen):
    """Intersect current/frozen restrictions; old missing policy stays disabled."""
    a = core.validate_policy(current.get("outreach", {}))
    b = core.validate_policy(frozen.get("outreach", {}))
    return {"enabled": a["enabled"] and b["enabled"],
            "business_days": max(a["business_days"], b["business_days"]),
            "daily_draft_cap": min(a["daily_draft_cap"], b["daily_draft_cap"]),
            "do_not_contact": sorted(set(a["do_not_contact"] + b["do_not_contact"]))}


def _run(root, run_id, now):
    import workspace as w
    if not run_id:
        raise settings.SettingsError("run:required: start a dedicated outreach run first")
    now = core._time(now)
    run = settings.load_run(root, run_id)  # Never start/reset a run here.
    current = settings.load_settings(root)
    frozen = settings.validate_settings(run["settings"])
    start = core._time(run["started_at"])
    if now < start:
        raise settings.SettingsError("run:not_started: outreach run has not started")
    for policy in (current, frozen):
        if policy["mode"] != "apply":
            raise settings.SettingsError(f"mode:{policy['mode']}: outreach is disabled")
        minutes = policy["per_run_minutes"]
        if minutes is not None and now.astimezone(timezone.utc) - start.astimezone(timezone.utc) >= timedelta(minutes=minutes):
            raise settings.SettingsError("cap:deadline: outreach run time limit reached")
    w.work_guard(root, run_id, now, "outreach")
    return now, run, current, frozen


def _checked_point(root, record, project_id, bullet_id):
    import workspace as w
    try:
        safe_evidence(root, "projects/catalog.json")
        project = w.current_projects(root).get(project_id)
        if not project or project.get("active", True) is not True:
            return None
        bullets = [b for b in project["bullets"] if b["id"] == bullet_id]
        if len(bullets) != 1 or not core._text(bullets[0].get("text")):
            return None
        point = {"project_id": project_id, "bullet_id": bullet_id,
                 "text": bullets[0]["text"],
                 "catalog_fingerprint": w.project_fingerprint(project)}
        saved = core._submission(record, point)
        if saved is None:
            return None
        # These are the exact historical files, never a newly resolved CV.
        safe_evidence(root, saved["exact_resume_copy"], saved["sha256"])
        safe_evidence(root, saved["confirmation_path"], saved["confirmation_sha256"])
        return point
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def _callbacks(root):
    import workspace as w
    def load():
        directory = root / "applications/records"
        if not directory.resolve().is_relative_to(root):
            raise core.OutreachError("Application records escape the workspace")
        for path in directory.glob("*/record.json"):
            safe_evidence(root, path)
        return w.records(root)  # Always reload durable records, including aliases.
    def persist(record):
        target = root / "applications/records" / w.slug(record["application_id"]) / "record.json"
        if not target.resolve().is_relative_to(root):
            raise core.OutreachError("Application record write escapes the workspace")
        w.persist_record(root, record)
    return load, persist


def _mutate(root, application_id, operation, deferred):
    """Export every attempted durable write, even an uncertain completion."""
    import workspace as w
    load, save = _callbacks(root)
    attempted = False
    def persist(record):
        nonlocal attempted
        attempted = True
        save(record)  # Dirty marker, atomic record, then index update.
    try:
        result = operation(load, persist)
    except BaseException:
        if attempted:
            # No created ID/status is fabricated on failed persistence.
            w.finish_mutation(root, {"application_id": application_id,
                                    "status": "outreach_recovery_required"}, deferred)
        raise
    return w.finish_mutation(root, result, deferred, repeated=not attempted)


def create_follow_up(root, run_id, application_id, contact_id, channel,
                     project_id, bullet_id, now, adapter=None, *, deferred=False):
    """CLI hook; existing run required, historical application run stays intact."""
    root = Path(root).resolve(strict=True)
    now, run, current, frozen = _run(root, run_id, now)
    policy = effective_policy(current, frozen)
    def operation(load, persist):
        records, record, _, _ = core._snapshot(load, application_id, ())
        zones = {current["timezone"], frozen["timezone"]}
        count = max(core._count(records, now, zone) for zone in zones)
        def blocked(reason):
            return {"application_id": application_id, "status": "blocked",
                    "reason": reason, "daily_count": count}
        if not policy["enabled"]:
            return blocked("disabled")
        # This run belongs to outreach, never to the past submission. Reject
        # accidental reuse of any application's original submission run.
        if any(r.get("run_id") == run_id or any(s.get("run_id") == run_id
               for s in r.get("submissions", [])) for r in records):
            raise settings.SettingsError("run:dedicated: use a separate outreach run")
        point = _checked_point(root, record, project_id, bullet_id)
        if point is None:
            return blocked("unverifiable_project_source")
        saved = core._submission(record, point)
        if any(now < core.business_day_due(saved["submitted_at"], policy["business_days"], zone) for zone in zones):
            return blocked("not_due")
        if count >= policy["daily_draft_cap"]:
            return blocked("daily_cap")
        def save(updated):
            for event in updated.get("outreach", []):
                if event.get("run_id") == run_id:
                    event.setdefault("settings_hash", settings.settings_hash(current))
                    event.setdefault("run_settings_hash", run["settings_hash"])
            persist(updated)
        return core.create_follow_up(application_id, load_records=load, persist_record=save,
            policy=policy, now=now, contact_id=contact_id, channel=channel,
            trusted_point=point, adapter=adapter, timezone_name=current["timezone"], run_id=run_id)
    return _mutate(root, application_id, operation, deferred)


def reconcile_follow_up(root, application_id, follow_up_id, now, *, resolution,
                        evidence, note, adapter_id=None, deferred=False):
    """Log explicit evidence-backed resolution; no run restart or adapter call."""
    root = Path(root).resolve(strict=True)
    checked = safe_evidence(root, evidence)
    def operation(load, persist):
        def save(record):
            event = core._find(record, follow_up_id)
            event["reconciliation"]["evidence_sha256"] = checked["sha256"]
            persist(record)
        return core.reconcile_follow_up(application_id, follow_up_id,
            load_records=load, persist_record=save, now=now, resolution=resolution,
            evidence=checked["path"], note=note, adapter_id=adapter_id)
    return _mutate(root, application_id, operation, deferred)


def confirm_manual_send(root, application_id, follow_up_id, *, occurred_at,
                        recorded_at, evidence, deferred=False):
    """Record actual user-confirmed send time, even after a stop/deadline."""
    root = Path(root).resolve(strict=True)
    checked = safe_evidence(root, evidence)
    def operation(load, persist):
        def save(record):
            event = core._find(record, follow_up_id)
            event["manual_sends"][-1]["evidence_sha256"] = checked["sha256"]
            persist(record)
        return core.confirm_manual_send(application_id, follow_up_id,
            load_records=load, persist_record=save, occurred_at=occurred_at,
            recorded_at=recorded_at, evidence=checked["path"])
    return _mutate(root, application_id, operation, deferred)
