#!/usr/bin/env python3
"""Token-free resume assembly, version releases and application bookkeeping."""

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from resolve_resume import ROOT, check_selection, current_projects, latest_release, project_fingerprint, resolve, timestamp
from export_dashboard import export_dashboard
from workspace_config import select_root
import seen_index
import settings
import answers as answer_core
from listing_input import ListingError, validate_listing, normalize_listing_contacts
import contacts
import safety
import scope_guard
import setup_workspace
import cleanup
import outcomes
import batches
import reviewer
import computer_fallback
import retry_policy
import eligibility
from export_recovery import after_mutation, recover, export_state, STATE_PATH as EXPORT_STATE_PATH
from zoneinfo import ZoneInfo
from uuid import uuid4

STATUSES = {"discovered", "preparing", "awaiting_answer", "ready", "submitted",
            "submission_uncertain", "failed", "skipped", "rejected", "interview", "withdrawn",
            "needs_review", "saved", "dry_run_complete", "acknowledged", "assessment", "offer"}
TRANSITIONS = {
    "needs_review": {"preparing", "skipped"},
    "discovered": {"preparing", "skipped"},
    "preparing": {"awaiting_answer", "ready", "failed", "skipped", "dry_run_complete"},
    "awaiting_answer": {"preparing", "skipped"},
    "ready": {"preparing", "submitted", "submission_uncertain", "failed", "skipped", "dry_run_complete"},
    "saved": {"preparing", "skipped"}, "dry_run_complete": {"preparing", "skipped"},
    "submission_uncertain": {"submitted", "failed", "withdrawn"},
    "failed": {"preparing", "skipped"}, "skipped": {"preparing"},
    "submitted": {"rejected", "interview", "withdrawn"},
    "interview": {"rejected", "withdrawn"}, "rejected": set(), "withdrawn": set(),
}
MAX_RETRIES = 3


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finish_mutation(root, result, deferred=False, repeated=False):
    if repeated and not export_state(root)["dirty"]:
        envelope = {"mutation": {**result, "ok": True}, "export": {"ok": True, "status": "unchanged"}}
    else:
        envelope = after_mutation(root, result, export_csv, deferred=deferred)
    # Preserve the existing local API while exposing independent mutation/export outcomes.
    return {**result, **envelope}


@contextmanager
def writer(root):
    with (root / ".workspace.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def slug(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise ValueError("IDs and scopes must be lowercase letters, digits and hyphens")
    return value


def text_tokens(text):
    text = re.sub(r"\[([^\]]+)\]\([^\n]*?\)", r"\1", text)
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def artifact_text(path):
    if path.suffix.lower() == ".md":
        return path.read_text()
    if path.suffix.lower() == ".pdf":
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in document.findall(".//w:p", ns))
    raise ValueError("Supported resume formats: Markdown, PDF and DOCX")


def canonical_url(value):
    parts = urlsplit(value.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("A complete HTTP(S) job URL without credentials is required")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    host = parts.hostname.lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parts.path)
        job_id = match[1] if match else query.get("currentJobId")
        if job_id and job_id.isdigit():
            return f"https://www.linkedin.com/jobs/view/{job_id}"
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_")
             and k.lower() not in {"trk", "trackingid", "refid", "position", "pagenum"}}
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query.items())), ""))


def draft(root, preset_name, now):
    preset = read(root / "resumes/presets.json")[preset_name]
    registry = read(root / "resumes/registry.json")
    master = resolve(registry, "master", "reference", now, root)
    projects = current_projects(root)
    selection, blocks = [], []
    for project_id in preset["projects"]:
        project = projects[project_id]
        bullets = project["bullets"][:preset["bullets_per_project"]] if project.get("active", True) else []
        if not bullets:
            raise ValueError(f"No reusable bullets for {project_id}")
        selection.append({"id": project_id, "updated_at": project["updated_at"],
                          "content_hash": project_fingerprint(project), "bullet_ids": [b["id"] for b in bullets]})
        blocks.append("### " + project["title"] + "\n\n" + "\n".join("- " + b["text"] for b in bullets))
    inputs = {"preset": preset_name, "preset_configuration": preset, "source_master_id": master["id"],
              "source_master_sha256": master["sha256"], "selected_projects": selection}
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:12]
    directory = root / "resumes/drafts" / slug(preset_name)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"draft-{key}.md"
    manifest_path = target.with_suffix(".manifest.json")
    text = Path(master["path"]).read_text()
    text = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S).lstrip()
    before, remainder = text.split("## Projects\n", 1)
    _, after = remainder.split("## Certifications\n", 1)
    text = before + "## Projects\n\n" + "\n\n".join(blocks) + "\n\n## Certifications\n" + after
    if target.exists() and target.read_text() != text:
        raise ValueError("Existing draft was edited; use a new revision rather than overwriting it")
    if not target.exists():
        target.write_text(text)
    manifest = {**inputs, "draft_id": f"{preset_name}-{key}", "status": "draft",
                "created_at": now.isoformat(), "path": str(target.relative_to(root)), "sha256": digest(target)}
    if manifest_path.exists():
        saved = read(manifest_path)
        if any(saved[k] != manifest[k] for k in inputs) or saved["sha256"] != manifest["sha256"]:
            raise ValueError("Existing draft manifest does not match its inputs")
    else:
        atomic_json(manifest_path, manifest)
    return {"draft": str(target), "manifest": str(manifest_path), "status": "draft"}


def release(root, args, now):
    registry = read(root / "resumes/registry.json")
    release_id, scope = slug(args.id), slug(args.scope)
    if any(r["id"] == release_id for r in registry["releases"]):
        raise ValueError("Release ID already exists; revisions cannot be overwritten")
    if args.review_days <= 0:
        raise ValueError("Review interval must be positive")
    source = args.file.resolve()
    if args.purpose == "application" and source.suffix.lower() not in (".pdf", ".docx"):
        raise ValueError("Application releases must be PDF or DOCX")
    if scope == "master" and (args.purpose != "reference" or source.suffix.lower() != ".md"):
        raise ValueError("The master scope is a Markdown reference")
    selected, master_id = [], None
    original_source, selected_with_master_id = None, None
    draft_provenance = None
    if getattr(args, "original", False):
        if scope == "master" or args.purpose != "application" or source.suffix.lower() != ".pdf" or args.manifest:
            raise ValueError("Original resume selection requires an application PDF and no draft manifest")
        if not source.is_relative_to(root.resolve()):
            raise ValueError("Archive the original PDF inside the workspace before selecting it")
        master = resolve(registry, "master", "reference", now, root)
        original_source = {"path": str(source.relative_to(root.resolve())), "sha256": digest(source)}
        selected_with_master_id = master["id"]
    elif scope != "master":
        if not args.manifest:
            raise ValueError("Variant releases require their draft manifest")
        draft_provenance = read(args.manifest)
        master = resolve(registry, "master", "reference", now, root)
        if (draft_provenance["source_master_id"] != master["id"] or
                draft_provenance["source_master_sha256"] != master["sha256"]):
            raise ValueError("Draft uses an older master; rebuild before release")
        draft_path = (root / draft_provenance["path"]).resolve()
        if not draft_path.is_relative_to(root.resolve()) or digest(draft_path) != draft_provenance["sha256"]:
            raise ValueError("Draft content no longer matches its manifest")
        selected = draft_provenance["selected_projects"]
        check_selection(selected, root)
        master_id = master["id"]
        if text_tokens(artifact_text(source)) != text_tokens(draft_path.read_text()):
            raise ValueError("Release text differs from the recorded draft; rebuild the draft or render the matching version")
    try:
        previous = latest_release(registry, scope, now)
    except ValueError as exc:
        if str(exc).startswith("No effective release"):
            previous = None
        else:
            raise
    target = root / "resumes/releases" / scope / (release_id + source.suffix.lower())
    if target.exists():
        raise ValueError("Release artifact already exists; inspect it before retrying")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    item = {"id": release_id, "scope": scope, "purpose": args.purpose, "status": "active",
            "path": str(target.relative_to(root)), "sha256": digest(target),
            "released_at": now.isoformat(), "effective_from": now.isoformat(),
            "review_due_at": (now + timedelta(days=args.review_days)).isoformat(), "valid_until": None,
            "supersedes": previous["id"] if previous else None, "superseded_by": None,
            "source_master_id": master_id, "selected_projects": selected,
            "original_source": original_source, "selected_with_master_id": selected_with_master_id,
            "draft_provenance": draft_provenance, "notes": args.note}
    if previous:
        previous.update(status="superseded", superseded_by=release_id)
    registry["releases"].append(item)
    atomic_json(root / "resumes/registry.json", registry)
    return {"id": release_id, "path": str(target), "replaces": item["supersedes"]}


def records(root):
    return [read(p) for p in sorted((root / "applications/records").glob("*/record.json"))]


def work_guard(root, run_id, now, command):
    try:
        safety.guard(root, run_id or os.environ.get("APPLYD_RUN_ID") or "local", now, command)
    except safety.SafetyBlocked:
        safety.notify_stop(root, now, safety.local_notification)
        raise
    # Capture only saves already observed facts. Keep its indexed duplicate
    # path free of record scans. Browser adapters must guard before navigation.
    if command != "capture":
        recovery = recover_submit(root, now)
        if recovery["recovered"]:
            finish_mutation(root, recovery)
            if recovery["requires_reconciliation"]:
                raise ValueError("submission_uncertain: reconcile the unfinished browser writer before new work")
        browser_retry_guard(root, now)


def retry_guard(record, now):
    decision = retry_policy.check_retry(record, now, MAX_RETRIES)
    if decision.decision == retry_policy.Decision.CONFIRMED:
        raise ValueError("Confirmed application cannot retry")
    if decision.decision == retry_policy.Decision.WAITING_ANSWER:
        raise ValueError("answer_validation: retry_blocked waiting_answer")
    if decision.decision == retry_policy.Decision.RECONCILIATION_REQUIRED:
        raise ValueError("retry_blocked: reconciliation_required; reconcile with saved evidence")
    if decision.decision != retry_policy.Decision.ELIGIBLE:
        raise ValueError("retry_blocked: " + decision.decision.value)
    return decision


def validate_browser_incident(incident, now):
    if not isinstance(incident, dict) or incident.get("reason") not in {"login", "page_changed"}:
        raise ValueError("Invalid browser retry incident")
    retry_policy.check_retry({"status": "failed", "retry_policy_version": 1, "retry_budget": MAX_RETRIES,
                              "retry_counts": {}, "blocker": incident, "next_eligible_at": None}, now, MAX_RETRIES)


def preserve_browser_incident(record):
    incident = record.get("blocker") or {}
    if ("browser_stop" not in record and incident.get("reason") in {"login", "page_changed"}
            and incident.get("resume") is None):
        return {**record, "browser_stop": json.loads(json.dumps(incident))}
    return record


def browser_retry_stops(root, now):
    stops = []
    for record in records(root):
        # Validate even an exhausted/uncertain record; malformed state must not
        # silently release a browser stop. The separate incident survives later
        # confirmation/reconciliation and replacement of the retry blocker.
        retry_policy.check_retry(record, now, MAX_RETRIES)
        incident = record.get("browser_stop", record.get("blocker"))
        if incident is None:
            if "browser_stop" in record:
                raise ValueError("Invalid browser retry incident")
            continue
        if not isinstance(incident, dict):
            raise ValueError("Invalid browser retry incident")
        if incident.get("reason") not in {"login", "page_changed"}:
            if "browser_stop" in record:
                raise ValueError("Invalid browser retry incident")
            continue
        validate_browser_incident(incident, now)
        if incident.get("resume") is not None and "browser_stop" in record:
            evidence_id = incident["resume"]["evidence_id"]
            evidence = root / "applications/records" / slug(record["application_id"]) / "retry-evidence" / (evidence_id + ".bin")
            content = evidence.read_bytes() if evidence.is_file() else b""
            sha = hashlib.sha256(content).hexdigest()
            if (not content.strip() or "evidence-" + sha != evidence_id
                    or not any(e.get("reason") == "browser_resumed" and e.get("retry_blocker_id") == incident["id"]
                               and e.get("evidence", {}).get("sha256") == sha for e in record["events"])):
                raise ValueError("Browser resume evidence is missing or changed")
        if incident.get("resume") is None:
            stops.append({"application_id": record["application_id"],
                          "blocker_id": incident["id"], "reason": incident["reason"]})
    return stops


def browser_retry_guard(root, now):
    if browser_retry_stops(root, now):
        raise ValueError("retry_browser_stopped: explicit retry-resume evidence required")


def persist_record(root, record):
    # Leave a durable recovery marker even if interrupted between record,
    # index and exports. The CLI holds its writer lock for the whole operation.
    atomic_json(root / EXPORT_STATE_PATH, {"schema_version": 1, "dirty": True, "status": "pending"})
    atomic_json(root / "applications/records" / slug(record["application_id"]) / "record.json", record)
    seen_index.update(root, record, canonical_url)


def save_retry_record(root, record, result, deferred=False):
    persist_record(root, record)
    return finish_mutation(root, result, deferred)


def retry_status(root, application_id, now):
    record = read(root / "applications/records" / slug(application_id) / "record.json")
    return {"application_id": application_id, **retry_policy.check_retry(record, now, MAX_RETRIES).as_dict(),
            "blocker_id": (record.get("blocker") or {}).get("id"),
            "retry_counts": record.get("retry_counts", {}),
            "retry_budget": min(MAX_RETRIES, record.get("retry_budget", MAX_RETRIES)),
            "blocked_on_answer_ids": record.get("blocked_on_answer_ids", []),
            "browser_stops": browser_retry_stops(root, now)}


def retry_failure(root, application_id, reason, now, deferred=False, *, event_details=None):
    try:
        reason = retry_policy.Reason(reason)
    except (ValueError, TypeError):
        raise ValueError("Unknown retry reason; use a Reason code") from None
    path = root / "applications/records" / slug(application_id) / "record.json"
    record = read(path)
    if "browser_stop" in record:
        validate_browser_incident(record["browser_stop"], now)
    updated = retry_policy.next_retry_state(preserve_browser_incident(record), reason, now, MAX_RETRIES)
    confirmed = retry_policy.check_retry(updated, now, MAX_RETRIES).decision == retry_policy.Decision.CONFIRMED
    if confirmed and reason not in {retry_policy.Reason.LOGIN, retry_policy.Reason.PAGE_CHANGED,
                                    retry_policy.Reason.ACCOUNT_SAFETY, retry_policy.Reason.MANUAL_STOP}:
        return finish_mutation(root, {"application_id": application_id, "decision": "confirmed",
                                      "already_recorded": True}, deferred, repeated=True)
    if reason in {retry_policy.Reason.ACCOUNT_SAFETY, retry_policy.Reason.MANUAL_STOP}:
        # Existing safety owns these stops and its screenshot/resume workflow.
        # Persist application state first, even if safety then demands evidence.
        try:
            result = save_retry_record(root, updated, {"application_id": application_id}, deferred)
        finally:
            safety.stop_run(root, record.get("run_id") or "local", reason.value, now)
        return {**result, "decision": "stopped"}
    if reason in {retry_policy.Reason.LOGIN, retry_policy.Reason.PAGE_CHANGED}:
        current = updated.get("browser_stop") or record.get("blocker")
        if not current or current.get("reason") not in {"login", "page_changed"} or current.get("resume") is not None:
            blocker = updated["blocker"]
            if (blocker and blocker["reason"] == reason.value and blocker["resume"] is None
                    and blocker != record.get("blocker")):
                current = json.loads(json.dumps(blocker))
            else:
                # A login can be observed while submission is uncertain. It
                # stops the session without weakening the uncertainty blocker.
                current = {"id": "browser-" + uuid4().hex,
                           "reason": reason.value, "blocked_at": now.isoformat(), "answer_ids": [], "resume": None}
        updated["browser_stop"] = json.loads(json.dumps(current))
    decision = retry_policy.check_retry(updated, now, MAX_RETRIES)
    if updated == record:
        return finish_mutation(root, {"application_id": application_id, **decision.as_dict(),
                                      "already_recorded": True}, deferred, repeated=True)
    status = (record["status"] if confirmed else
              "submission_uncertain" if decision.decision == retry_policy.Decision.RECONCILIATION_REQUIRED else
              "awaiting_answer" if updated.get("blocked_on_answer_ids") else "failed")
    updated.update(status=status, status_updated_at=now.isoformat())
    updated["events"].append({"at": now.isoformat(), **(event_details or {}), "status": status, "reason": reason.value,
                              "retry_blocker_id": (updated.get("blocker") or {}).get("id")})
    return save_retry_record(root, updated, {"application_id": application_id, **decision.as_dict(),
                                           "blocker_id": (updated.get("browser_stop") or updated.get("blocker") or {}).get("id")}, deferred)


def retry_resume(root, application_id, blocker_id, evidence_path, now, deferred=False):
    directory = root / "applications/records" / slug(application_id)
    record = read(directory / "record.json")
    decision = retry_policy.check_retry(record, now, MAX_RETRIES)
    incident = record.get("browser_stop", record.get("blocker"))
    if (not isinstance(incident, dict) or incident.get("id") != blocker_id or incident.get("reason") not in {"login", "page_changed"}
            or incident.get("resume") is not None):
        raise ValueError("Resume requires the exact current browser blocker incident")
    validate_browser_incident(incident, now)
    if now < timestamp(incident["blocked_at"]):
        raise ValueError("Resume evidence predates the blocker")
    content = Path(evidence_path).read_bytes()
    if not content.strip():
        raise ValueError("Resume requires nonempty saved evidence")
    sha = hashlib.sha256(content).hexdigest()
    evidence_id = "evidence-" + sha
    # Save immutable bytes before linking them from either incident. run.json
    # remains the original settings snapshot throughout the stop and resume.
    target = directory / "retry-evidence" / (evidence_id + ".bin")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError("Saved resume evidence was modified")
    else:
        fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if decision.decision == retry_policy.Decision.RESUME_REQUIRED and (record.get("blocker") or {}).get("id") == blocker_id:
        record = retry_policy.resume_retry(record, blocker_id, evidence_id, now, MAX_RETRIES)
    # Clearing a session incident after confirmation/uncertainty/exhaustion
    # permits independent jobs only. The application's retry gate still denies
    # work, and neither its budget nor its uncertainty blocker is removed.
    record["browser_stop"] = {**incident, "resume": {"evidence_id": evidence_id, "at": now.isoformat()}}
    evidence = {"path": str(target.relative_to(root)), "sha256": sha}
    record["events"].append({"at": now.isoformat(), "status": record["status"], "reason": "browser_resumed",
                              "retry_blocker_id": blocker_id, "evidence": evidence})
    return save_retry_record(root, record, {"application_id": application_id, "blocker_id": blocker_id,
                                           "browser_resumed": True,
                                           "decision": retry_policy.check_retry(record, now, MAX_RETRIES).decision}, deferred)


def check_scope(root, record, url, now):
    policies = [settings.load_settings(root)]
    if record.get("run_id"):
        policies.append(settings.load_run(root, record["run_id"])["settings"])
    if all(scope_guard.allowed(url, policy) for policy in policies):
        return {"allowed": True}
    updated = retry_policy.next_retry_state(record, "off_scope", now, MAX_RETRIES)
    record.update(updated)
    record.update(stop_reason="off_scope", off_scope_url=url, status_updated_at=now.isoformat())
    if not record.get("submissions") and record["status"] != "submission_uncertain":
        record["status"] = "failed"
    record["events"].append({"at": now.isoformat(), "status": record["status"], "reason": "off_scope"})
    persist_record(root, record)
    finish_mutation(root, {"application_id": record["application_id"], "reason": "off_scope"})
    raise ValueError("off_scope: application destination is outside the run allowlist")


def listing_decision(root, record, now):
    listing = dict(record)
    if record.get("job_description_path"):
        path = root / record["job_description_path"]
        if digest(path) != record.get("job_description_sha256"):
            raise ValueError("eligibility: captured description changed")
        listing["job_description"] = path.read_text()
    bank_path = root / "profile/answers.json"
    facts = eligibility.confirmed_facts(read(bank_path) if bank_path.exists() else {"answers": []}, listing, now)
    policies = [settings.load_settings(root)]
    if record.get("run_id"):
        policies.append(settings.load_run(root, record["run_id"])["settings"])
    decisions = [eligibility.evaluate(listing, policy, facts) for policy in policies]
    decision = "excluded" if any(d["decision"] == "excluded" for d in decisions) else "needs_review" if any(d["decision"] == "needs_review" for d in decisions) else "eligible"
    return {"decision": decision, "reason_codes": sorted({code for d in decisions for code in d["reason_codes"]})}


def require_eligible(root, record, now):
    decision = listing_decision(root, record, now)
    if decision["decision"] != "eligible":
        raise ValueError("eligibility_" + decision["decision"] + ": " + ",".join(decision["reason_codes"]))
    return decision


def preflight(root, record, now):
    work_guard(root, record.get("run_id"), now, "preflight")
    if record.get("application_id"):
        # A stale in-memory ready snapshot must not bypass a newer durable wait.
        retry_guard(read(root / "applications/records" / slug(record["application_id"]) / "record.json"), now)
    retry_guard(record, now)
    if record["status"] != "ready":
        raise ValueError("Only a ready application may pass submission preflight")
    prepared = record.get("prepared")
    if not prepared:
        raise ValueError("Application has no prepared submission snapshot")
    if record.get("submissions") or record.get("submission_intent"):
        raise ValueError("Confirmed or unfinished submission requires reconciliation; never replay Submit")
    settings.check_submission(root, record, now)
    require_eligible(root, record, now)
    check_scope(root, record, record.get("application_url") or record.get("destination_url") or record["canonical_listing_url"], now)
    if record.get("source") != "linkedin_job_page":
        raise ValueError("source: current LinkedIn job-page capture is required before submission")
    current = resolve(read(root / "resumes/registry.json"), prepared["scope"], "application", now, root)
    if current["id"] != prepared["id"]:
        raise ValueError("Prepared resume has been replaced; prepare again")
    if digest(root / prepared["exact_resume_copy"]) != prepared["sha256"] or digest(root / prepared["answers_path"]) != prepared["answers_sha256"]:
        raise ValueError("Prepared submission snapshot was modified")
    validation = answer_core.validate_snapshot(root, record, read(root / prepared["answers_path"]), now)
    if not validation["valid"]:
        raise ValueError("answer_validation: exact answers are unknown, expired, changed or outside scope")
    if not prepared.get("review_snapshot"):
        raise ValueError("review_required: review the current filled form before submission")
    review_path = root / prepared["review_snapshot"]
    if digest(review_path) != prepared.get("review_sha256"):
        raise ValueError("review_mismatch: stored form review was modified")
    saved_review = read(review_path)
    verdict = reviewer.validate_verdict(saved_review["verdict"], prepared, read(root / prepared["answers_path"]), saved_review["visible"])
    if not verdict["valid"] or not verdict["passed"]:
        raise ValueError("review_mismatch: see the private form-review differences before retrying")
    return {"application_id": record["application_id"], "ready": True, "resume_id": current["id"]}


def begin_submit(root, application_id, observed_url, session_id, now, deferred=False):
    """Reserve the browser and persist uncertainty before authorizing one click."""
    record_path = root / "applications/records" / slug(application_id) / "record.json"
    record = read(record_path)
    if canonical_url(observed_url) != record["canonical_listing_url"]:
        raise ValueError("observed_job_mismatch: browser job differs from the prepared application")
    preflight(root, record, now)
    if (root / "applications/computer-use.lock").exists():
        raise ValueError("browser_claimed: computer-use writer is active; reconcile before Submit")
    keys = set(seen_index.identity_keys(record, canonical_url(record["listing_url"])))
    for other in records(root):
        if other.get("submissions") and keys.intersection(seen_index.identity_keys(other, canonical_url(other["listing_url"]))):
            raise ValueError("duplicate_submission: a confirmation already exists")
    claim_path = root / "applications/browser-claim.json"
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_id = record["prepared"]["attempt_id"]
    claim = {"application_id": application_id, "attempt_id": attempt_id,
             "session_id": slug(session_id), "observed_url": canonical_url(observed_url),
             "occurred_at": now.isoformat()}
    try:
        fd = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("browser_claimed: reconcile the existing writer/intent before retrying") from exc
    with os.fdopen(fd, "w") as stream:
        json.dump(claim, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    record = retry_policy.next_retry_state(record, "submission_uncertain", now, MAX_RETRIES)
    if (root / "applications/computer-use.lock").exists():
        # Keep the reserved claim: recovery must reconcile the interrupted intent.
        raise ValueError("browser_claimed: computer-use writer raced with Submit; reconcile the saved claim")
    record.update(status="submission_uncertain", status_updated_at=now.isoformat(), submission_intent=claim)
    record["events"].append({"at": now.isoformat(), "status": "submission_uncertain", "reason": "submit_intent", "attempt_id": attempt_id})
    persist_record(root, record)
    return finish_mutation(root, {"application_id": application_id, "attempt_id": attempt_id, "click_authorized": True,
            "on_interruption": "submission_uncertain; reconcile with evidence before retry"}, deferred)


def recover_submit(root, now):
    """A crash in the claim/record window is uncertainty, never a new click."""
    path = root / "applications/browser-claim.json"
    if not path.exists():
        return {"recovered": False}
    claim = read(path)
    record_path = root / "applications/records" / slug(claim["application_id"]) / "record.json"
    record = read(record_path)
    if record.get("submissions"):
        path.unlink()
        return {"recovered": True, "application_id": claim["application_id"], "requires_reconciliation": False}
    else:
        record = retry_policy.next_retry_state(preserve_browser_incident(record), "submission_uncertain", now, MAX_RETRIES)
        record.update(status="submission_uncertain", status_updated_at=now.isoformat(), submission_intent=claim)
        if not any(e.get("attempt_id") == claim["attempt_id"] for e in record["events"]):
            record["events"].append({"at": now.isoformat(), "status": "submission_uncertain", "reason": "recovered_intent", "attempt_id": claim["attempt_id"]})
        persist_record(root, record)
    return {"recovered": True, "application_id": claim["application_id"], "requires_reconciliation": True}


def capture(root, data, now, deferred=False):
    work_guard(root, data.get("run_id") if isinstance(data, dict) else None, now, "capture")
    data = normalize_listing_contacts(data, now)
    if data.get("source") != "linkedin_job_page":
        raise ListingError("source", "only linkedin_job_page facts are accepted")
    policy = settings.load_settings(root)
    reason = settings.eligibility(data, policy)
    if data.get("run_id"):
        frozen = settings.load_run(root, data["run_id"])["settings"]
        reason = reason or settings.eligibility(data, frozen)
    required = ("company", "role", "listing_url") if reason else ("company", "role", "location", "listing_url", "job_description")
    for key in required:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing non-empty job field: {key}")
    if reason:
        data.setdefault("location", "")
        data.setdefault("job_description", "")
    decision = listing_decision(root, data, now)
    reason = reason or (decision["reason_codes"][0] if decision["decision"] != "eligible" else None)
    canonical = canonical_url(data["listing_url"])
    if not (root / "applications/index/.initialized").exists():
        seen_index.reindex(root, records(root), canonical_url)
    old = seen_index.lookup(root, data, canonical)
    if old:
        if old["key"].startswith("xpost-"):
            raise ValueError(f"Possible cross-post/repost of {old['application_id']}; reconcile before capturing again")
        return finish_mutation(root, {"duplicate": True, "application_id": old["application_id"], "status": old["status"]}, deferred, repeated=True)
    application_id = "app-" + now.strftime("%Y-%m-%d") + "-" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
    directory = root / "applications/records" / application_id
    directory.mkdir(parents=True, exist_ok=False)
    description = data.pop("job_description")
    (directory / "job-description.txt").write_text(description)
    status = ("skipped" if decision["decision"] == "excluded" else "needs_review") if reason else "saved" if policy["mode"] == "save_only" else "discovered"
    record = {**data, "application_id": application_id, "canonical_listing_url": canonical,
              "captured_at": now.isoformat(), "status": status, "status_updated_at": now.isoformat(),
              "job_description_path": str((directory / "job-description.txt").relative_to(root)),
              "job_description_sha256": digest(directory / "job-description.txt"),
              "events": [{"at": now.isoformat(), "status": status, "reason": reason}], "submissions": [],
              "stop_reason": reason, "eligibility": decision}
    atomic_json(directory / "record.json", record)
    seen_index.update(root, record, canonical_url)
    return finish_mutation(root, {"application_id": application_id, "record": str(directory / "record.json"), "duplicate": False, "status": status, "reason": reason}, deferred)


def prepare_answers(root, application_id, questions, now, deferred=False):
    path = root / "applications/records" / slug(application_id) / "record.json"
    record = read(path)
    work_guard(root, record.get("run_id"), now, "prepare-answers")
    retry_guard(record, now)
    if record.get("submissions") or record["status"] in {"submitted", "submission_uncertain"}:
        raise ValueError("Cannot prepare answers for a confirmed or uncertain submission")
    require_eligible(root, record, now)
    prepared = answer_core.prepare_answers(root, record, questions, now)
    if not prepared["ready"]:
        record = retry_policy.next_retry_state(record, "unknown_answer", now, MAX_RETRIES,
                                               blocked_answer_ids=prepared["blocked_on_answer_ids"])
    record.update(status=prepared["status"], status_updated_at=now.isoformat(),
                  blocked_on_answer_ids=prepared["blocked_on_answer_ids"],
                  answer_preparation={k: prepared[k] for k in ("answers_path", "snapshot_version", "bank_version")})
    record["events"].append({"at": now.isoformat(), "status": prepared["status"], "reason": "unknown_answer" if not prepared["ready"] else "answers_prepared"})
    return save_retry_record(root, record, prepared, deferred)


def requeue_answers(root, now):
    def resume_check(root, record, now):
        scope = record.get("prepared", {}).get("scope") or settings.load_settings(root)["resume_scope"]
        if not scope:
            return False
        resolve(read(root / "resumes/registry.json"), scope, "application", now, root)
        return True
    def eligible(root, record, now):
        return listing_decision(root, record, now)["decision"] == "eligible"
    result = answer_core.requeue(root, now, resume_check=resume_check, eligibility_check=eligible)
    runnable, waiting = [], []
    for update in result["updates"] + result["waiting"]:
        path = root / "applications/records" / update["application_id"] / "record.json"
        record = read(path)
        # The resolver retains missing/unrepresented IDs and returns actual
        # unresolved IDs. Only its confirmed difference may release a blocker.
        resolved = set(record.get("blocked_on_answer_ids", [])) - set(update["blocked_on_answer_ids"])
        updated = retry_policy.resolve_answer_blocker(record, resolved, now, MAX_RETRIES)
        if update["blocked_on_answer_ids"]:
            updated = retry_policy.next_retry_state(updated, "unknown_answer", now, MAX_RETRIES,
                                                     blocked_answer_ids=update["blocked_on_answer_ids"])
        decision = retry_policy.check_retry(updated, now, MAX_RETRIES)
        reasons = list(update.get("reasons", []))
        if decision.decision != retry_policy.Decision.ELIGIBLE:
            reasons.append("retry_" + decision.decision.value)
        if browser_retry_stops(root, now):
            reasons.append("browser_stopped")
        if reasons:
            waiting.append({**update, "status": "awaiting_answer", "reasons": reasons})
        else:
            runnable.append(update)
            updated.update(status="preparing", resumed_from_answers=True)
        if updated != record:
            updated["status_updated_at"] = now.isoformat()
            updated["events"].append({"at": now.isoformat(), "status": updated["status"], "reason": "answers_resolved",
                                       "resolved_answer_ids": sorted(resolved)})
            persist_record(root, updated)
    result.update(updates=runnable, runnable_ids=[u["application_id"] for u in runnable], waiting=waiting)
    return result


def review_form(root, application_id, visible, now, deferred=False):
    path = root / "applications/records" / slug(application_id) / "record.json"
    record = read(path)
    work_guard(root, record.get("run_id"), now, "review-form")
    retry_guard(record, now)
    if record["status"] != "ready" or record.get("submission_intent") or record.get("submissions"):
        raise ValueError("Form review requires one ready, unsubmitted attempt")
    prepared = record["prepared"]
    verdict = reviewer.review(prepared, read(root / prepared["answers_path"]), visible)
    snapshot = {"visible": visible, "verdict": verdict}
    content = (json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n").encode()
    sha = hashlib.sha256(content).hexdigest()
    target = (root / prepared["answers_path"]).parent / ("review-" + sha + ".json")
    if not target.exists():
        atomic_json(target, snapshot)
    elif digest(target) != sha:
        raise ValueError("Review snapshot already exists with changed bytes")
    prepared.update(review_snapshot=str(target.relative_to(root)), review_sha256=sha)
    if verdict["passed"] and record.get("stop_reason") == "review_mismatch":
        record["stop_reason"] = None
    elif not verdict["passed"]:
        record["stop_reason"] = "review_mismatch"
    record["events"].append({"at": now.isoformat(), "status": "ready", "reason": "review_passed" if verdict["passed"] else "review_mismatch"})
    persist_record(root, record)
    return finish_mutation(root, {"application_id": application_id, "passed": verdict["passed"],
                                "stop_reason": verdict["stop_reason"], "review_snapshot": prepared["review_snapshot"],
                                "difference_codes": [d["code"] for d in verdict["differences"]]}, deferred)


def event(root, args, now, deferred=False):
    directory = root / "applications/records" / slug(args.id)
    record = read(directory / "record.json")
    status = args.status
    try:
        occurred = batches.validate_event_time(args.at) if getattr(args, "at", None) else now
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("event_at requires an ISO timestamp with a UTC offset") from exc
    details = {**batches.event_metadata(occurred, now), "status": status, "note": args.note}
    if status in outcomes.OUTCOME_STATUSES and (record.get("submissions") or record.get("submitted_at")):
        evidence = None
        if args.evidence:
            sha = digest(args.evidence)
            target = directory / "outcomes" / ("evidence-" + sha + args.evidence.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(args.evidence, target)
            evidence = {"path": str(target.relative_to(root)), "sha256": sha}
        updated = outcomes.apply_outcome(record, status, occurred.isoformat(), now.isoformat(),
                                        evidence=evidence, note=args.note,
                                        follow_up_at=getattr(args, "follow_up_at", None),
                                        follow_up_days=settings.load_settings(root)["follow_up_days"])
        if updated.get("outcome_events") == record.get("outcome_events"):
            return finish_mutation(root, {"application_id": args.id, "status": record["status"], "already_recorded": True}, deferred, repeated=True)
        updated["events"].append(details)
        updated.update(status=updated["outcome"], status_updated_at=now.isoformat())
        persist_record(root, updated)
        return finish_mutation(root, {"application_id": args.id, "status": updated["status"]}, deferred)
    if status in {"preparing", "ready"}:
        require_eligible(root, record, now)
        work_guard(root, record.get("run_id"), now, "event")
        if status == "preparing" and retry_policy.check_retry(record, now, MAX_RETRIES).decision == retry_policy.Decision.WAITING_ANSWER:
            return finish_mutation(root, {"application_id": args.id, "status": record["status"],
                                          "decision": "waiting_answer", "already_recorded": True}, deferred, repeated=True)
        retry_guard(record, now)
    uncertain = (record["status"] == "submission_uncertain" or record.get("submission_intent") is not None
                 or record.get("stop_reason") == "submission_uncertain"
                 or (record.get("blocker") or {}).get("reason") == "submission_uncertain")
    reconciling = uncertain and status in {"failed", "withdrawn"}
    submission_observation = (status in {"submitted", "submission_uncertain"} and record.get("prepared")
                              and not (record.get("submissions") or record.get("submitted_at")))
    if status not in STATUSES or status == "discovered":
        raise ValueError("Invalid event status")
    if status == record["status"] and not reconciling:
        return finish_mutation(root, {"application_id": args.id, "status": status, "already_recorded": True}, deferred, repeated=True)
    if status == "submitted" and (record.get("submissions") or record.get("submitted_at")):
        return finish_mutation(root, {"application_id": args.id, "status": record["status"], "already_recorded": True}, deferred, repeated=True)
    if (status not in TRANSITIONS.get(record["status"], set()) and not submission_observation
            and not (uncertain and status in {"submitted", "failed", "withdrawn"})):
        raise ValueError(f"Invalid transition {record['status']} -> {status}; reconcile before retrying")
    if reconciling and (not args.evidence or not args.note or not args.note.strip()):
        raise ValueError("Resolving uncertainty without submission requires reconciliation evidence and a note")
    if reconciling and not args.evidence.read_bytes().strip():
        raise ValueError("Resolving uncertainty requires nonempty saved evidence")
    if status == "failed" and not reconciling:
        return retry_failure(root, args.id, getattr(args, "reason", None) or "unsupported", now, deferred,
                             event_details=details)
    if status == "ready":
        if not args.resume_scope or not args.answers:
            raise ValueError("Ready requires --resume-scope and an exact questions/answers JSON file")
        resume = resolve(read(root / "resumes/registry.json"), args.resume_scope, "application", now, root)
        run_id = getattr(args, "run_id", None) or record.get("run_id")
        if run_id:
            settings.load_run(root, run_id)
            record["run_id"] = run_id
        answers = read(args.answers)
        if not isinstance(answers, list) or any(not isinstance(x, dict) or "question" not in x or "answer" not in x for x in answers):
            raise ValueError("Answers must be a JSON list with exact question and answer fields (an empty list is allowed)")
        if record.get("blocked_on_answer_ids") or not answer_core.validate_snapshot(root, record, answers, now)["valid"]:
            raise ValueError("answer_validation: prepare current known answers before ready")
        attempts = directory / "submissions"
        attempts.mkdir(parents=True, exist_ok=True)
        attempt = Path(tempfile.mkdtemp(prefix=now.strftime("%Y%m%dT%H%M%S%f") + "-", dir=attempts))
        target = attempt / Path(resume["path"]).name
        shutil.copy2(resume["path"], target)
        atomic_json(attempt / "answers.json", answers)
        record["prepared"] = {**resume, "prepared_at": now.isoformat(), "run_id": run_id, "attempt_id": attempt.name,
                              "exact_resume_copy": str(target.relative_to(root)),
                              "answers_path": str((attempt / "answers.json").relative_to(root)),
                              "answers_sha256": digest(attempt / "answers.json")}
        batch_id = getattr(args, "batch_id", None) or record.get("batch_id")
        category = getattr(args, "category", None) or record.get("category")
        record["prepared"] = batches.attach_attempt_context(record["prepared"], batch_id, category)
        record.update(batch_id=batch_id, category=category)
    if status in {"submitted", "submission_uncertain"}:
        prepared = record.get("prepared")
        if not prepared:
            raise ValueError("Prepare and snapshot the exact resume/answers before submission")
        if status == "submitted":
            if not args.evidence or not args.evidence.is_file():
                raise ValueError("Submitted status requires a saved confirmation evidence file")
            # Confirmed historical submissions must still be logged after a deadline
            # or replacement; the saved ID/hash records what was actually prepared.
            try:
                preflight(root, {**record, "status": "ready", "submission_intent": None}, now)
            except (ValueError, OSError) as exc:
                details["recording_warning"] = str(exc)
            # Guard recovery may have durably recorded a crashed claim. Keep
            # that incident and its counters when logging the late confirmation.
            record = read(directory / "record.json")
            evidence = directory / ("confirmation-" + now.strftime("%Y%m%dT%H%M%S%f") + args.evidence.suffix)
            shutil.copy2(args.evidence, evidence)
            details.update(confirmation_path=str(evidence.relative_to(root)), confirmation_sha256=digest(evidence))
            record["submissions"].append({**prepared, "submitted_at": occurred.isoformat(), **details})
            record["submitted_at"] = occurred.isoformat()
    if reconciling:
        evidence = directory / ("reconciliation-" + now.strftime("%Y%m%dT%H%M%S%f") + args.evidence.suffix)
        shutil.copy2(args.evidence, evidence)
        details.update(reconciliation_path=str(evidence.relative_to(root)), reconciliation_sha256=digest(evidence))
    if status == "submission_uncertain":
        record = retry_policy.next_retry_state(preserve_browser_incident(record), "submission_uncertain", now, MAX_RETRIES)
    if details.get("confirmation_path") or details.get("reconciliation_path"):
        # Clear uncertainty only in this evidence-backed reconciliation path.
        # Counts, budget and independent browser incidents remain untouched.
        if (record.get("blocker") or {}).get("reason") == "submission_uncertain":
            details["reconciled_retry_blocker"] = record["blocker"]
            record.update(blocker=None, next_eligible_at=None)
        if record.get("stop_reason") == "submission_uncertain":
            record["stop_reason"] = None
        record.pop("submission_intent", None)
    record["events"].append(details)
    record.update(status=status, status_updated_at=now.isoformat())
    persist_record(root, record)
    if details.get("confirmation_path") or details.get("reconciliation_path"):
        claim_path = root / "applications/browser-claim.json"
        if claim_path.exists() and read(claim_path).get("application_id") == args.id:
            claim_path.unlink()
    return finish_mutation(root, {"application_id": args.id, "status": status}, deferred)


def export_csv(root, snapshot=None):
    snapshot = records(root) if snapshot is None else list(snapshot)
    fields = ["contacts", "application_id", "company", "role", "location", "platform", "platform_job_id",
              "employer_requisition_id", "listing_url", "application_url", "captured_at", "status",
              "status_updated_at", "resume_id", "source_master_id", "resume_sha256", "record_path",
              "submitted_at", "confirmation_path", "notes", "outcome", "latest_outcome_at", "follow_up_at"]
    target = root / "applications/log.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".csv-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", newline="") as stream:
            output = csv.DictWriter(stream, fieldnames=fields)
            output.writeheader()
            for record in snapshot:
                saved = record["submissions"][-1] if record["submissions"] else record.get("prepared", {})
                row = {key: record.get(key, "") for key in fields}
                row.update(resume_id=saved.get("id", ""), source_master_id=saved.get("source_master_id", ""),
                           resume_sha256=saved.get("sha256", ""), confirmation_path=saved.get("confirmation_path", ""),
                           record_path=f"applications/records/{record['application_id']}/record.json")
                row["contacts"] = contacts.contacts_json(record.get("contacts"))
                output.writerow({k: "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    dashboard = export_dashboard(root, snapshot=snapshot)
    return {"path": str(target), "records": len(snapshot), "dashboard": dashboard["path"]}


def contact_change(root, application_id, now, *, add=None, remove=None, email_text=None, deferred=False):
    path = root / "applications/records" / slug(application_id) / "record.json"
    record = read(path)
    if add is not None:
        description = (root / record["job_description_path"]).read_text()
        updated = contacts.add_contact(record, add, now, jd_text=description, email_text=email_text)
    else:
        updated = contacts.remove_contact(record, remove)
    persist_record(root, updated)
    return finish_mutation(root, {"application_id": application_id, "contacts": updated.get("contacts", [])}, deferred)


def fallback_session(root, application_id, run_id, session_id, plan, now, adapter=None, clock=None):
    clock = clock or (lambda: datetime.now(ZoneInfo(settings.load_settings(root)["timezone"])))
    def guard(phase):
        current = clock()
        record = read(root / "applications/records" / slug(application_id) / "record.json")
        if record.get("run_id") != run_id or record.get("source") != "linkedin_job_page":
            raise ValueError("fallback_run_or_source_mismatch")
        work_guard(root, run_id, current, "computer-use")
        retry_guard(record, current)
        require_eligible(root, record, current)
        settings.check_submission(root, {**record, "status": "ready"}, current)
        check_scope(root, record, plan["initial_url"], current)
        prepared = record.get("answer_preparation") or {}
        if not prepared.get("answers_path") or not answer_core.validate_snapshot(root, record, read(root / prepared["answers_path"]), current)["valid"]:
            raise ValueError("answer_validation")
        return True
    def current_resume():
        record = read(root / "applications/records" / slug(application_id) / "record.json")
        scope = record.get("prepared", {}).get("scope") or settings.load_settings(root)["resume_scope"]
        return Path(resolve(read(root / "resumes/registry.json"), scope, "application", clock(), root)["path"])
    try:
        actions = [computer_fallback.Action(**value) for value in plan["actions"]]
        result = computer_fallback.run_workspace_session(root, run_id, session_id, guard=guard,
            adapter=adapter, clock=clock, initial_tab_id=plan["initial_tab_id"], initial_url=plan["initial_url"],
            failure_kind=plan["failure_kind"], dom_failed=plan.get("dom_failed") is True,
            actions=actions, resume_resolver=current_resume)
        return {"outcome": result.outcome.value, "reason": result.reason,
                "step_count": result.step_count, "record_ref": result.record_ref}
    except (ValueError, KeyError, TypeError, OSError):
        return {"outcome": "failed", "reason": "fallback_storage_or_input_blocker", "step_count": None, "record_ref": None}


def import_settings(root, change, deferred=False):
    if (not isinstance(change, dict) or set(change) != {"schema_version", "base_hash", "changes"}
            or type(change["schema_version"]) is not int or change["schema_version"] != 1
            or not isinstance(change["changes"], dict) or not change["changes"]):
        raise ValueError("Invalid settings-change schema")
    current = settings.load_settings(root)
    if change["base_hash"] != settings.settings_hash(current):
        raise ValueError("Stale settings base; refresh before importing")
    updated = settings._validate({**current, **change["changes"]})
    atomic_json(root / "settings.json", updated)
    return finish_mutation(root, {"persisted": True, "settings_hash": settings.settings_hash(updated)}, deferred)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Private data root (overrides APPLYD_WORKSPACE)")
    parser.add_argument("--defer-export", action="store_true", help="Persist mutations now and run export once after the batch")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("init", help="Initialize a fresh empty private workspace")
    commands.add_parser("doctor", help="Inspect local prerequisites without browser actions")
    projects = commands.add_parser("projects")
    projects.add_argument("--id")
    projects.add_argument("--tag")
    projects.add_argument("--bullets-only", action="store_true")
    answer = commands.add_parser("answer")
    answer.add_argument("--id", required=True)
    build = commands.add_parser("draft")
    build.add_argument("--preset", required=True)
    publish = commands.add_parser("release")
    publish.add_argument("--file", type=Path, required=True)
    publish.add_argument("--id", required=True)
    publish.add_argument("--scope", required=True)
    publish.add_argument("--purpose", choices=("reference", "application"), required=True)
    publish.add_argument("--manifest", type=Path)
    publish.add_argument("--original", action="store_true", help="Register a user-selected original PDF without changing its bytes")
    publish.add_argument("--review-days", type=int, default=30)
    publish.add_argument("--note", required=True)
    new = commands.add_parser("capture")
    source = new.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--stdin", action="store_true")
    new.add_argument("--run-id")
    new.add_argument("--batch-id")
    new.add_argument("--category")
    update = commands.add_parser("event")
    update.add_argument("--id", required=True)
    update.add_argument("--status", choices=sorted(STATUSES - {"discovered"}), required=True)
    update.add_argument("--note", default="")
    update.add_argument("--reason", choices=[r.value for r in retry_policy.Reason],
                        help="Typed failure reason; unclassified failed events stop as unsupported")
    update.add_argument("--resume-scope")
    update.add_argument("--answers", type=Path)
    update.add_argument("--evidence", type=Path)
    update.add_argument("--run-id")
    update.add_argument("--at", help="Actual event occurrence, with UTC offset; recorded_at remains the current time")
    update.add_argument("--follow-up-at")
    update.add_argument("--batch-id")
    update.add_argument("--category")
    check = commands.add_parser("preflight")
    check.add_argument("--id", required=True)
    failure = commands.add_parser("retry-failure", help="Record one observed typed failure; never run a retry loop")
    failure.add_argument("--id", required=True)
    failure.add_argument("--reason", choices=[r.value for r in retry_policy.Reason], required=True)
    retry_check = commands.add_parser("retry-status", help="Read durable retry eligibility and session browser stops")
    retry_check.add_argument("--id", required=True)
    retry_continue = commands.add_parser("retry-resume", help="Explicitly resume the exact login/page-change incident")
    retry_continue.add_argument("--id", required=True)
    retry_continue.add_argument("--blocker-id", required=True)
    retry_continue.add_argument("--evidence", type=Path, required=True)
    browser_check = commands.add_parser("browser-guard", help="Require this gate immediately before each browser action")
    browser_check.add_argument("--run-id")
    browser_check.add_argument("--id", help="Also require this application's retry eligibility")
    commands.add_parser("export")
    commands.add_parser("reindex")
    run = commands.add_parser("run-start")
    run.add_argument("--id", required=True)
    screen = commands.add_parser("screen", help="Check a result-card header before extraction or navigation")
    screen.add_argument("--file", type=Path, required=True)
    begin = commands.add_parser("begin-submit")
    begin.add_argument("--id", required=True)
    begin.add_argument("--observed-url", required=True)
    begin.add_argument("--session-id", required=True)
    commands.add_parser("recover-submit")
    prepare = commands.add_parser("prepare-answers")
    prepare.add_argument("--id", required=True)
    prepare.add_argument("--questions", type=Path, required=True)
    context = commands.add_parser("context")
    context.add_argument("--run-id")
    import_answers = commands.add_parser("import-answers")
    import_answers.add_argument("--file", type=Path, required=True)
    import_answers.add_argument("--preview", action="store_true")
    commands.add_parser("requeue")
    settings_import = commands.add_parser("settings-import")
    settings_import.add_argument("--file", type=Path, required=True)
    safety_check = commands.add_parser("safety-check")
    safety_check.add_argument("--file", type=Path, required=True, help="Current-page text and URL JSON")
    safety_check.add_argument("--run-id", required=True)
    safety_check.add_argument("--screenshot")
    resume_stop = commands.add_parser("resume-stop")
    resume_stop.add_argument("--run-id", required=True)
    resume_stop.add_argument("--operator", required=True)
    resume_stop.add_argument("--note", required=True)
    scope = commands.add_parser("scope-check")
    scope.add_argument("--id", required=True)
    scope.add_argument("--url", required=True)
    summary = commands.add_parser("run-summary")
    summary.add_argument("--run-id", required=True)
    cleanup_run = commands.add_parser("cleanup-run")
    cleanup_run.add_argument("--run-id", required=True)
    cleanup_run.add_argument("--execute", action="store_true")
    tab = commands.add_parser("register-tab")
    tab.add_argument("--run-id", required=True)
    tab.add_argument("--window-id", required=True, type=int)
    tab.add_argument("--tab-id", required=True, type=int)
    tab.add_argument("--url", required=True)
    tab.add_argument("--id", required=True)
    progress = commands.add_parser("batch-progress")
    progress.add_argument("--batch-id", required=True)
    progress.add_argument("--target", type=int, required=True)
    progress.add_argument("--categories", type=Path)
    review = commands.add_parser("review-form")
    review.add_argument("--id", required=True)
    review.add_argument("--visible", type=Path, required=True)
    eligible = commands.add_parser("eligibility", help="Evaluate captured full listing without a model")
    eligible.add_argument("--id", required=True)
    contact = commands.add_parser("contact")
    contact.add_argument("--id", required=True)
    change = contact.add_mutually_exclusive_group(required=True)
    change.add_argument("--add", type=Path)
    change.add_argument("--remove")
    contact.add_argument("--email-evidence", type=Path)
    fallback = commands.add_parser("computer-use", help="Validate a bounded fallback plan; an adapter is required to execute")
    fallback.add_argument("--id", required=True)
    fallback.add_argument("--run-id", required=True)
    fallback.add_argument("--session-id", required=True)
    fallback.add_argument("--plan", type=Path, required=True)
    for command in ("next-action", "run"):
        runner_command = commands.add_parser(command, help="Plan or execute bounded durable actions through an adapter")
        runner_command.add_argument("--run-id", required=True)
        runner_command.add_argument("--batch", type=Path, required=True)
        if command == "run":
            runner_command.add_argument("--steps", type=int, default=1)
    outreach_command = commands.add_parser("outreach", help="Create one capped local note or use a supplied draft-only adapter")
    outreach_command.add_argument("--run-id", required=True)
    outreach_command.add_argument("--id", required=True)
    outreach_command.add_argument("--contact-id", required=True)
    outreach_command.add_argument("--channel", choices=("email", "linkedin"), required=True)
    outreach_command.add_argument("--project-id", required=True)
    outreach_command.add_argument("--bullet-id", required=True)
    outreach_reconcile = commands.add_parser("outreach-reconcile")
    outreach_reconcile.add_argument("--id", required=True)
    outreach_reconcile.add_argument("--follow-up-id", required=True)
    outreach_reconcile.add_argument("--resolution", choices=("draft_created", "note_created", "not_created"), required=True)
    outreach_reconcile.add_argument("--evidence", type=Path, required=True)
    outreach_reconcile.add_argument("--note", required=True)
    outreach_reconcile.add_argument("--adapter-id")
    outreach_sent = commands.add_parser("outreach-confirm-send", help="Record an already completed user-confirmed send; perform no send")
    outreach_sent.add_argument("--id", required=True)
    outreach_sent.add_argument("--follow-up-id", required=True)
    outreach_sent.add_argument("--at", required=True)
    outreach_sent.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = select_root(args.workspace)
    try:
        if args.command == "init":
            result = setup_workspace.init_workspace(root)
            result = finish_mutation(root, result)
            print(json.dumps(result, indent=2))
            return
        if args.command == "doctor":
            result = setup_workspace.doctor(root)
            print(json.dumps(result, indent=2))
            return
        policy = settings.load_settings(root)
        now = datetime.now(ZoneInfo(policy["timezone"]))
        if args.command in {"next-action", "run"}:
            from runner_workspace import build_runner
            controller = build_runner(root, args.run_id, args.batch)
            if args.command == "next-action":
                result = controller.plan_next(now).as_dict()
            else:
                result = {"actions": [a.as_dict() for a in controller.execute(clock=lambda: datetime.now(ZoneInfo(settings.load_settings(root)["timezone"])), max_steps=args.steps)]}
            with writer(root):
                if export_state(root)["dirty"]:
                    result["export"] = recover(root, export_csv) if not args.defer_export else {"status": "deferred", "ok": False}
            print(json.dumps(result, indent=2))
            return
        with writer(root):
            if args.command == "status":
                registry = read(root / "resumes/registry.json")
                scopes = sorted({r["scope"] for r in registry["releases"]})
                result = {"resumes": [], "projects": len(current_projects(root)), "application_records": len(records(root))}
                for scope in scopes:
                    try:
                        candidate = latest_release(registry, scope, now)
                        result["resumes"].append(resolve(registry, scope, candidate["purpose"], now, root))
                    except ValueError as exc:
                        result["resumes"].append({"scope": scope, "blocked": str(exc)})
                result["unanswered"] = [a["id"] for a in read(root / "profile/answers.json")["answers"] if a["value"] is None]
            elif args.command == "projects":
                items = current_projects(root).values()
                result = [([b["text"] for b in p["bullets"]] if args.bullets_only else p) if args.id else {"id": p["id"], "updated_at": p["updated_at"], "tags": p["tags"], "active": p.get("active", True)}
                          for p in items if (not args.id or p["id"] == args.id) and (not args.tag or args.tag in p["tags"])]
                if args.id and args.bullets_only:
                    result = result[0] if result else []
            elif args.command == "draft":
                result = draft(root, args.preset, now)
            elif args.command == "answer":
                matches = [a for a in read(root / "profile/answers.json")["answers"] if a["id"] == args.id]
                if len(matches) != 1:
                    raise ValueError("Answer ID is missing or ambiguous")
                result = matches[0]
                if result["value"] is None:
                    raise ValueError("Answer is unknown; queue this question")
                if any(result.get(k) and now >= timestamp(result[k]) for k in ("review_due_at", "valid_until")):
                    raise ValueError("Answer is overdue or expired; queue this question")
            elif args.command == "release":
                result = release(root, args, now)
            elif args.command == "capture":
                try:
                    data = validate_listing(json.load(sys.stdin) if args.stdin else read(args.file))
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise ListingError("input", "invalid JSON") from exc
                if args.run_id:
                    data["run_id"] = args.run_id
                data.update(batches.attach_attempt_context({}, args.batch_id, args.category))
                result = capture(root, data, now, args.defer_export)
            elif args.command == "event":
                result = event(root, args, now, args.defer_export)
            elif args.command == "preflight":
                result = preflight(root, read(root / "applications/records" / slug(args.id) / "record.json"), now)
            elif args.command == "retry-failure":
                result = retry_failure(root, args.id, args.reason, now, args.defer_export)
            elif args.command == "retry-status":
                result = retry_status(root, args.id, now)
            elif args.command == "retry-resume":
                result = retry_resume(root, args.id, args.blocker_id, args.evidence, now, args.defer_export)
            elif args.command == "browser-guard":
                work_guard(root, args.run_id, now, "browser-guard")
                if args.id:
                    retry_guard(read(root / "applications/records" / slug(args.id) / "record.json"), now)
                result = {"browser_allowed": True}
            elif args.command == "reindex":
                result = seen_index.reindex(root, records(root), canonical_url)
            elif args.command == "run-start":
                work_guard(root, args.id, now, "run-start")
                result = settings.run_start(root, args.id, now)
                context = answer_core.context(root, now)
                atomic_json(root / "applications/runs" / args.id / ("context-" + context["version"] + ".json"), context)
                result["context_version"] = context["version"]
            elif args.command == "screen":
                data = read(args.file)
                reason = settings.eligibility(data, policy)
                result = capture(root, data, now) if reason else {"eligible": True}
            elif args.command == "begin-submit":
                result = begin_submit(root, args.id, args.observed_url, args.session_id, now, args.defer_export)
            elif args.command == "recover-submit":
                result = recover_submit(root, now)
                if result["recovered"]:
                    result = finish_mutation(root, result, args.defer_export)
            elif args.command == "prepare-answers":
                result = prepare_answers(root, args.id, read(args.questions), now, args.defer_export)
            elif args.command == "context":
                result = answer_core.context(root, now)
                if args.run_id:
                    settings.load_run(root, args.run_id)
                    atomic_json(root / "applications/runs" / args.run_id / ("context-" + result["version"] + ".json"), result)
            elif args.command == "import-answers":
                result = answer_core.import_answers(root, read(args.file), now, preview=args.preview)
                if not args.preview:
                    result["queue"] = requeue_answers(root, now)
                    answer_core.context(root, now)
                    result = finish_mutation(root, result, args.defer_export)
            elif args.command == "requeue":
                result = requeue_answers(root, now)
                result = finish_mutation(root, result, args.defer_export)
            elif args.command == "settings-import":
                result = import_settings(root, read(args.file), args.defer_export)
            elif args.command == "safety-check":
                page = read(args.file)
                detection = safety.detect(page.get("text", ""), page.get("url", ""))
                if detection:
                    evidence = {"screenshot": args.screenshot, "kind": detection.kind} if args.screenshot else None
                    incident = safety.stop_run(root, args.run_id, "account_safety", now, evidence, safety.local_notification)
                    raise safety.SafetyBlocked(incident)
                work_guard(root, args.run_id, now, "safety-check")
                result = {"safe": True}
            elif args.command == "resume-stop":
                incident = safety.resume_stop(root, args.run_id, now, {"operator": args.operator, "note": args.note}, "resume-stop")
                result = {"resolved": True, "stop_id": incident["stop_id"]}
            elif args.command == "scope-check":
                record = read(root / "applications/records" / slug(args.id) / "record.json")
                work_guard(root, record.get("run_id"), now, "scope-check")
                retry_guard(record, now)
                result = check_scope(root, record, args.url, now)
            elif args.command == "run-summary":
                result = cleanup.run_summary(root, args.run_id, now)
            elif args.command == "cleanup-run":
                result = cleanup.cleanup_run(root, args.run_id, now, execute=args.execute, export_callback=lambda: recover(root, export_csv))
            elif args.command == "register-tab":
                work_guard(root, args.run_id, now, "register-tab")
                retry_guard(read(root / "applications/records" / slug(args.id) / "record.json"), now)
                result = cleanup.register_tab(root, args.run_id, args.window_id, args.tab_id, args.url, args.id, now)
            elif args.command == "batch-progress":
                result = batches.batch_progress(records(root), args.batch_id, args.target, read(args.categories) if args.categories else None)
            elif args.command == "eligibility":
                result = listing_decision(root, read(root / "applications/records" / slug(args.id) / "record.json"), now)
            elif args.command == "contact":
                result = contact_change(root, args.id, now, add=read(args.add) if args.add else None,
                                        remove=args.remove, email_text=args.email_evidence.read_text() if args.email_evidence else None,
                                        deferred=args.defer_export)
            elif args.command == "computer-use":
                result = fallback_session(root, args.id, args.run_id, args.session_id, read(args.plan), now)
            elif args.command == "outreach":
                from outreach_workspace import create_follow_up
                result = create_follow_up(root, args.run_id, args.id, args.contact_id, args.channel, args.project_id, args.bullet_id, now, deferred=args.defer_export)
            elif args.command == "outreach-reconcile":
                from outreach_workspace import reconcile_follow_up
                result = reconcile_follow_up(root, args.id, args.follow_up_id, now, resolution=args.resolution, evidence=args.evidence, note=args.note, adapter_id=args.adapter_id, deferred=args.defer_export)
            elif args.command == "outreach-confirm-send":
                from outreach_workspace import confirm_manual_send
                result = confirm_manual_send(root, args.id, args.follow_up_id, occurred_at=args.at, recorded_at=now, evidence=args.evidence, deferred=args.defer_export)
            elif args.command == "review-form":
                result = review_form(root, args.id, read(args.visible), now, args.defer_export)
            else:
                result = {"export": recover(root, export_csv)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.command == "outreach" and result["status"] in {"blocked", "draft_uncertain"}:
            parser.exit(2)
        if args.command == "computer-use" and result["outcome"] != "completed":
            parser.exit(2)
        if args.command == "export" and not result["export"]["ok"]:
            parser.exit(1)
    except safety.SafetyError as exc:
        parser.exit(2, f"{exc}\n")
    except ListingError as exc:
        parser.exit(2, f"listing_validation: {exc}\n")
    except (ValueError, KeyError, OSError, TypeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
