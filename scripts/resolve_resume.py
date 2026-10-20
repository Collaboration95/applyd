#!/usr/bin/env python3
"""Resolve a dated resume release; reject stale or superseded artifacts."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("All release times must include a UTC offset")
    return parsed


def latest_release(registry, scope, now):
    releases = [
        item for item in registry["releases"]
        if item["scope"] == scope
        and item.get("released_at")
        and timestamp(item["released_at"]) <= now
        and timestamp(item["effective_from"]) <= now
    ]
    if not releases:
        raise ValueError(f"No effective release exists for scope: {scope}")
    latest_time = max(timestamp(item["effective_from"]) for item in releases)
    latest = [item for item in releases if timestamp(item["effective_from"]) == latest_time]
    if len(latest) != 1:
        raise ValueError(f"Ambiguous latest release for scope: {scope}")
    return latest[0]


def check_release(registry, release, now, root):
    release_id = release["id"]
    if release["status"] != "active":
        raise ValueError(f"Latest release {release_id} is {release['status']}; do not fall back")
    if release.get("superseded_by"):
        replacement = next((r for r in registry["releases"] if r["id"] == release["superseded_by"]), None)
        if replacement is None:
            raise ValueError(f"Missing replacement for {release_id}")
        if replacement["scope"] != release["scope"]:
            raise ValueError(f"Replacement scope mismatch for {release_id}")
        if replacement.get("released_at") and timestamp(replacement["released_at"]) <= now and timestamp(replacement["effective_from"]) <= now:
            raise ValueError(f"Release {release_id} has been replaced; do not fall back")
    for field in ("valid_until", "review_due_at"):
        if release.get(field) and now >= timestamp(release[field]):
            raise ValueError(f"Release {release_id} passed {field}; update before use")
    if not release.get("review_due_at"):
        raise ValueError(f"Release {release_id} needs a review deadline")
    artifact = (root / release["path"]).resolve()
    if not artifact.is_relative_to(root.resolve()):
        raise ValueError("Resume path must stay inside the workspace")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != release["sha256"]:
        raise ValueError(f"File hash mismatch for {release_id}; create a new revision")
    return artifact


def current_projects(root=ROOT):
    records = json.loads((root / "projects/catalog.json").read_text())["projects"]
    result = {}
    for record in records:
        if record["id"] in result:
            raise ValueError("Duplicate project IDs")
        result[record["id"]] = record
    return result


def project_fingerprint(project):
    return hashlib.sha256(json.dumps(project, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_selection(selection, root=ROOT):
    projects = current_projects(root)
    for item in selection:
        project = projects.get(item["id"])
        if project is None or project_fingerprint(project) != item.get("content_hash"):
            raise ValueError(f"Project {item['id']} has a replacement; rebuild the resume")
        reusable = {b["id"] for b in project["bullets"]} if project.get("active", True) else set()
        if not set(item["bullet_ids"]).issubset(reusable):
            raise ValueError(f"Selected project bullets are no longer reusable: {item['id']}")


def resolve(registry, scope, purpose, now, root=ROOT):
    ids = [item["id"] for item in registry["releases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate resume release IDs")
    release = latest_release(registry, scope, now)
    artifact = check_release(registry, release, now, root)
    if release["purpose"] != purpose:
        raise ValueError(f"Release {release['id']} is for {release['purpose']}, not {purpose}")
    check_selection(release.get("selected_projects", []), root)
    if purpose == "application":
        if artifact.suffix.lower() not in (".pdf", ".docx"):
            raise ValueError("Application release must point to a PDF or DOCX")
    if scope != "master":
        master = latest_release(registry, "master", now)
        check_release(registry, master, now, root)
        original = release.get("original_source")
        if original:
            original_path = (root / original["path"]).resolve()
            if not original_path.is_relative_to(root.resolve()) or hashlib.sha256(original_path.read_bytes()).hexdigest() != original["sha256"] or original["sha256"] != release["sha256"]:
                raise ValueError("Selected original PDF changed; do not use it")
            if release.get("selected_with_master_id") != master["id"]:
                raise ValueError("Master changed since this original PDF was selected; review the selection")
        elif release.get("source_master_id") != master["id"]:
            raise ValueError("Variant uses an older master; regenerate/review before use")
    return {"id": release["id"], "scope": scope, "purpose": purpose,
            "path": str(artifact), "sha256": release["sha256"],
            "source_master_id": release.get("source_master_id"),
            "original_source": release.get("original_source"),
            "selected_projects": release.get("selected_projects", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--purpose", choices=("reference", "application"), default="application")
    args = parser.parse_args()
    try:
        registry = json.loads((ROOT / "resumes/registry.json").read_text())
        result = resolve(registry, args.scope, args.purpose, datetime.now(timezone.utc))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        parser.exit(1, f"BLOCKED: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
