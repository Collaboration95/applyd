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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from resolve_resume import ROOT, check_selection, current_projects, latest_release, project_fingerprint, resolve, timestamp
from export_dashboard import export_dashboard

STATUSES = {"discovered", "preparing", "awaiting_answer", "ready", "submitted",
            "submission_uncertain", "failed", "skipped", "rejected", "interview", "withdrawn"}
TRANSITIONS = {
    "discovered": {"preparing", "skipped"},
    "preparing": {"awaiting_answer", "ready", "failed", "skipped"},
    "awaiting_answer": {"preparing", "skipped"},
    "ready": {"preparing", "submitted", "submission_uncertain", "failed", "skipped"},
    "submission_uncertain": {"submitted", "failed", "withdrawn"},
    "failed": {"preparing", "skipped"}, "skipped": {"preparing"},
    "submitted": {"rejected", "interview", "withdrawn"},
    "interview": {"rejected", "withdrawn"}, "rejected": set(), "withdrawn": set(),
}


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
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def preflight(root, record, now):
    if record["status"] != "ready":
        raise ValueError("Only a ready application may pass submission preflight")
    prepared = record.get("prepared")
    if not prepared:
        raise ValueError("Application has no prepared submission snapshot")
    current = resolve(read(root / "resumes/registry.json"), prepared["scope"], "application", now, root)
    if current["id"] != prepared["id"]:
        raise ValueError("Prepared resume has been replaced; prepare again")
    if digest(root / prepared["exact_resume_copy"]) != prepared["sha256"] or digest(root / prepared["answers_path"]) != prepared["answers_sha256"]:
        raise ValueError("Prepared submission snapshot was modified")
    return {"application_id": record["application_id"], "ready": True, "resume_id": current["id"]}


def capture(root, data, now):
    for key in ("company", "role", "location", "listing_url", "job_description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing non-empty job field: {key}")
    canonical = canonical_url(data["listing_url"])
    def normal(value):
        return " ".join(value.casefold().split())
    identity = [normal(data[k]) for k in ("company", "role", "location")]
    for old in records(root):
        same_platform_id = (data.get("platform_job_id") and old.get("platform_job_id") == data["platform_job_id"]
                            and old.get("platform") == data.get("platform"))
        same_requisition = (data.get("employer_requisition_id") and old.get("employer_requisition_id") == data["employer_requisition_id"]
                            and normal(old["company"]) == normal(data["company"]))
        if old["canonical_listing_url"] == canonical or same_platform_id or same_requisition:
            return {"duplicate": True, "application_id": old["application_id"], "status": old["status"]}
        if [normal(old[k]) for k in ("company", "role", "location")] == identity:
            raise ValueError(f"Possible cross-post/repost of {old['application_id']}; reconcile before capturing again")
    application_id = "app-" + now.strftime("%Y-%m-%d") + "-" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
    directory = root / "applications/records" / application_id
    directory.mkdir(parents=True, exist_ok=False)
    description = data.pop("job_description")
    (directory / "job-description.txt").write_text(description)
    record = {**data, "application_id": application_id, "canonical_listing_url": canonical,
              "captured_at": now.isoformat(), "status": "discovered", "status_updated_at": now.isoformat(),
              "job_description_path": str((directory / "job-description.txt").relative_to(root)),
              "job_description_sha256": digest(directory / "job-description.txt"),
              "events": [{"at": now.isoformat(), "status": "discovered"}], "submissions": []}
    atomic_json(directory / "record.json", record)
    export_csv(root)
    return {"application_id": application_id, "record": str(directory / "record.json"), "duplicate": False}


def event(root, args, now):
    directory = root / "applications/records" / slug(args.id)
    record = read(directory / "record.json")
    status = args.status
    if status not in STATUSES or status == "discovered":
        raise ValueError("Invalid event status")
    if status == record["status"]:
        return {"application_id": args.id, "status": status, "already_recorded": True}
    if status not in TRANSITIONS[record["status"]]:
        raise ValueError(f"Invalid transition {record['status']} -> {status}; reconcile before retrying")
    if record["status"] == "submission_uncertain" and status != "submitted" and (not args.evidence or not args.note):
        raise ValueError("Resolving uncertainty without submission requires reconciliation evidence and a note")
    details = {"at": now.isoformat(), "status": status, "note": args.note}
    if status == "ready":
        if not args.resume_scope or not args.answers:
            raise ValueError("Ready requires --resume-scope and an exact questions/answers JSON file")
        resume = resolve(read(root / "resumes/registry.json"), args.resume_scope, "application", now, root)
        answers = read(args.answers)
        if not isinstance(answers, list) or any(not isinstance(x, dict) or "question" not in x or "answer" not in x for x in answers):
            raise ValueError("Answers must be a JSON list with exact question and answer fields (an empty list is allowed)")
        attempt = directory / "submissions" / now.strftime("%Y%m%dT%H%M%S%f")
        attempt.mkdir(parents=True)
        target = attempt / Path(resume["path"]).name
        shutil.copy2(resume["path"], target)
        atomic_json(attempt / "answers.json", answers)
        record["prepared"] = {**resume, "prepared_at": now.isoformat(),
                              "exact_resume_copy": str(target.relative_to(root)),
                              "answers_path": str((attempt / "answers.json").relative_to(root)),
                              "answers_sha256": digest(attempt / "answers.json")}
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
                preflight(root, {**record, "status": "ready"}, now)
            except (ValueError, OSError) as exc:
                details["recording_warning"] = str(exc)
            evidence = directory / ("confirmation-" + now.strftime("%Y%m%dT%H%M%S%f") + args.evidence.suffix)
            shutil.copy2(args.evidence, evidence)
            details.update(confirmation_path=str(evidence.relative_to(root)), confirmation_sha256=digest(evidence))
            record["submissions"].append({**prepared, "submitted_at": now.isoformat(), **details})
            record["submitted_at"] = now.isoformat()
    if record["status"] == "submission_uncertain" and status != "submitted":
        evidence = directory / ("reconciliation-" + now.strftime("%Y%m%dT%H%M%S%f") + args.evidence.suffix)
        shutil.copy2(args.evidence, evidence)
        details.update(reconciliation_path=str(evidence.relative_to(root)), reconciliation_sha256=digest(evidence))
    record["events"].append(details)
    record.update(status=status, status_updated_at=now.isoformat())
    atomic_json(directory / "record.json", record)
    export_csv(root)
    return {"application_id": args.id, "status": status}


def export_csv(root):
    fields = ["application_id", "company", "role", "location", "platform", "platform_job_id",
              "employer_requisition_id", "listing_url", "application_url", "captured_at", "status",
              "status_updated_at", "resume_id", "source_master_id", "resume_sha256", "record_path",
              "submitted_at", "confirmation_path", "notes"]
    target = root / "applications/log.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".csv-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", newline="") as stream:
            output = csv.DictWriter(stream, fieldnames=fields)
            output.writeheader()
            for record in records(root):
                saved = record["submissions"][-1] if record["submissions"] else record.get("prepared", {})
                row = {key: record.get(key, "") for key in fields}
                row.update(resume_id=saved.get("id", ""), source_master_id=saved.get("source_master_id", ""),
                           resume_sha256=saved.get("sha256", ""), confirmation_path=saved.get("confirmation_path", ""),
                           record_path=f"applications/records/{record['application_id']}/record.json")
                output.writerow({k: "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    dashboard = export_dashboard(root)
    return {"path": str(target), "records": len(records(root)), "dashboard": dashboard["path"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
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
    new.add_argument("--file", type=Path, required=True)
    update = commands.add_parser("event")
    update.add_argument("--id", required=True)
    update.add_argument("--status", choices=sorted(STATUSES - {"discovered"}), required=True)
    update.add_argument("--note", default="")
    update.add_argument("--resume-scope")
    update.add_argument("--answers", type=Path)
    update.add_argument("--evidence", type=Path)
    check = commands.add_parser("preflight")
    check.add_argument("--id", required=True)
    commands.add_parser("export")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).astimezone()
    try:
        with writer(ROOT):
            if args.command == "status":
                registry = read(ROOT / "resumes/registry.json")
                scopes = sorted({r["scope"] for r in registry["releases"]})
                result = {"resumes": [], "projects": len(current_projects(ROOT)), "application_records": len(records(ROOT))}
                for scope in scopes:
                    try:
                        candidate = latest_release(registry, scope, now)
                        result["resumes"].append(resolve(registry, scope, candidate["purpose"], now, ROOT))
                    except ValueError as exc:
                        result["resumes"].append({"scope": scope, "blocked": str(exc)})
                result["unanswered"] = [a["id"] for a in read(ROOT / "profile/answers.json")["answers"] if a["value"] is None]
            elif args.command == "projects":
                items = current_projects(ROOT).values()
                result = [([b["text"] for b in p["bullets"]] if args.bullets_only else p) if args.id else {"id": p["id"], "updated_at": p["updated_at"], "tags": p["tags"], "active": p.get("active", True)}
                          for p in items if (not args.id or p["id"] == args.id) and (not args.tag or args.tag in p["tags"])]
                if args.id and args.bullets_only:
                    result = result[0] if result else []
            elif args.command == "draft":
                result = draft(ROOT, args.preset, now)
            elif args.command == "answer":
                matches = [a for a in read(ROOT / "profile/answers.json")["answers"] if a["id"] == args.id]
                if len(matches) != 1:
                    raise ValueError("Answer ID is missing or ambiguous")
                result = matches[0]
                if result["value"] is None:
                    raise ValueError("Answer is unknown; queue this question")
                if any(result.get(k) and now >= timestamp(result[k]) for k in ("review_due_at", "valid_until")):
                    raise ValueError("Answer is overdue or expired; queue this question")
            elif args.command == "release":
                result = release(ROOT, args, now)
            elif args.command == "capture":
                result = capture(ROOT, read(args.file), now)
            elif args.command == "event":
                result = event(ROOT, args, now)
            elif args.command == "preflight":
                result = preflight(ROOT, read(ROOT / "applications/records" / slug(args.id) / "record.json"), now)
            else:
                result = export_csv(ROOT)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, KeyError, OSError, TypeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
