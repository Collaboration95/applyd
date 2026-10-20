#!/usr/bin/env python3
"""Export application records for the static, double-clickable run log."""

import json
import argparse
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from workspace_config import select_root
from settings import load_settings
from dashboard_payload import enrich_payload

ROOT = Path(__file__).resolve().parents[1]
SGT = ZoneInfo("Asia/Singapore")


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def local_file(root, relative):
    if not relative:
        return None
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return path


def export_dashboard(root=ROOT, snapshot=None):
    root = root.resolve()
    settings = load_settings(root)
    applications = []
    if snapshot is None:
        snapshot = [json.loads(source.read_text()) for source in sorted((root / "applications/records").glob("*/record.json"))]
    snapshot = list(snapshot)
    for record in snapshot:
        def resume(saved):
            path = local_file(root, saved.get("exact_resume_copy"))
            return {"id": saved.get("id"), "file": path.name if path else None,
                    "url": "../" + path.relative_to(root).as_posix() if path else None}
        events = [{"at": e.get("at"), "occurred_at": e.get("occurred_at") or e.get("at"),
                   "recorded_at": e.get("recorded_at"), "status": e.get("status"), "note": e.get("note", "")}
                  for e in record.get("events", [])]
        submissions = [{"at": s.get("submitted_at"), "resume": resume(s),
                        "applied_by": s.get("applied_by") or record.get("applied_by")}
                       for s in record.get("submissions", [])]
        prepared = record.get("prepared")
        description = local_file(root, record.get("job_description_path"))
        applications.append({"id": record["application_id"], "company": record["company"],
            "title": record["role"], "location": record.get("location", ""),
            "status": record["status"], "applied_by": record.get("applied_by"),
            "job_url": record.get("canonical_listing_url") or record.get("listing_url"),
            "application_url": record.get("application_url"),
            "captured_at": record.get("captured_at"), "updated_at": record.get("status_updated_at"),
            "description": description.read_text() if description else "",
            "notes": record.get("notes", ""), "events": events, "submissions": submissions,
            "contacts": record.get("contacts") or [],
            "outreach": deepcopy(record.get("outreach") or []),
            "outreach_blocker": deepcopy(record.get("outreach_blocker")),
            "outcome": record.get("outcome"), "latest_outcome_at": record.get("latest_outcome_at"),
            "follow_up_at": record.get("follow_up_at"),
            "prepared": {"at": prepared.get("prepared_at"), "resume": resume(prepared)} if prepared else None})
    applications.sort(key=lambda app: app.get("updated_at") or "", reverse=True)
    generated_at = datetime.now(ZoneInfo(settings["timezone"])).isoformat()
    payload = {"timezone": settings["timezone"], "settings": settings, "generated_at": generated_at, "applications": applications}
    payload = enrich_payload(root, snapshot, payload, generated_at)
    directory = root / "dashboard/data"
    write_json(directory / "applications.json", payload)
    # Classic script works from file://; browsers commonly block fetch() of local JSON.
    write_text(root / "dashboard/data.js", "window.JOB_SEARCH_DATA = " + json.dumps(payload, ensure_ascii=True) + ";\n")
    return {"path": str(root / "dashboard/index.html"), "applications": len(applications)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    root = select_root(parser.parse_args().workspace)
    from workspace import writer, export_csv
    from export_recovery import recover
    with writer(root):
        result = recover(root, export_csv)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
