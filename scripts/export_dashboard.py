#!/usr/bin/env python3
"""Export application records for the static, double-clickable run log."""

import json
import argparse
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from workspace_config import select_root

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


def export_dashboard(root=ROOT):
    root = root.resolve()
    applications = []
    for source in sorted((root / "applications/records").glob("*/record.json")):
        record = json.loads(source.read_text())
        def resume(saved):
            path = local_file(root, saved.get("exact_resume_copy"))
            return {"id": saved.get("id"), "file": path.name if path else None,
                    "url": "../" + path.relative_to(root).as_posix() if path else None}
        events = [{"at": e.get("at"), "status": e.get("status"), "note": e.get("note", "")}
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
            "prepared": {"at": prepared.get("prepared_at"), "resume": resume(prepared)} if prepared else None})
    applications.sort(key=lambda app: app.get("updated_at") or "", reverse=True)
    generated_at = datetime.now(SGT).isoformat()
    payload = {"timezone": "Asia/Singapore", "generated_at": generated_at, "applications": applications}
    directory = root / "dashboard/data"
    write_json(directory / "applications.json", payload)
    # Classic script works from file://; browsers commonly block fetch() of local JSON.
    write_text(root / "dashboard/data.js", "window.JOB_SEARCH_DATA = " + json.dumps(payload, ensure_ascii=True) + ";\n")
    return {"path": str(root / "dashboard/index.html"), "applications": len(applications)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    print(json.dumps(export_dashboard(select_root(parser.parse_args().workspace)), indent=2))
