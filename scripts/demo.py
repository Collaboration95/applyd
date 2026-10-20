#!/usr/bin/env python3
"""Build a synthetic application dashboard in a caller-selected directory."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from answers import _rows, bank_version
from settings import _validate, settings_hash


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "app.js", "runs.js", "control.js", "style.css")
SGT = ZoneInfo("Asia/Singapore")


def synthetic_applications() -> list[dict]:
    """Read only the explicitly fictional public fixture, never local records."""
    payload = json.loads((ROOT / "demo/applications.json").read_text())
    if payload.get("synthetic") is not True or len(payload.get("applications", [])) != 15:
        raise ValueError("Invalid synthetic demo fixture")
    return payload["applications"]


def synthetic_payload(generated_at):
    apps = synthetic_applications()
    policy = _validate({"mode": "dry_run", "timezone": "Asia/Singapore", "easy_apply_only": True,
                        "per_run_cap": 5, "per_run_minutes": 20})
    bank = {"schema_version": 1, "answers": []}
    questions = [{"question": "Preferred development language?", "type": "single_select", "options": ["Python", "JavaScript"]}]
    groups = {}
    for app in apps:
        if app["status"] != "awaiting_answer":
            continue
        record = {"application_id": app["id"], "company": app["company"], "role": app["title"]}
        rows = _rows(bank, record, questions, datetime.fromisoformat("2026-10-20T12:00:00+08:00"))
        app["blocked_on_answer_ids"] = [row["question_id"] for row in rows]
        for row in rows:
            group = groups.setdefault(row["question_id"], {"question_id": row["question_id"], "status": "unresolved", "application_ids": [], "occurrences": []})
            group["application_ids"].append(app["id"])
            group["occurrences"].append({**row, "company": app["company"]})
    return {"synthetic": True, "timezone": policy["timezone"], "generated_at": generated_at,
            "settings": policy, "settings_hash": settings_hash(policy), "bank_version": bank_version(bank),
            "answer_sources": [], "answer_receipts": [], "inbox": {"schema_version": 1, "questions": list(groups.values())},
            "applications": apps}


def build(destination: Path, generated_at: str | None = None) -> Path:
    destination = destination.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Demo destination must be empty; choose a fresh directory")
    dashboard = destination / "dashboard"
    dashboard.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        shutil.copyfile(ROOT / "dashboard" / asset, dashboard / asset)

    # Alternate entry point selects the existing Runs tab only on this generated fixture.
    run_html = (dashboard / "index.html").read_text().replace("</body>", """<script>window.addEventListener('load',()=>document.querySelector('[data-tab="runs"]').click());</script></body>""")
    (dashboard / "runs.html").write_text(run_html)
    payload = synthetic_payload(generated_at or datetime.now(SGT).isoformat(timespec="seconds"))
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (dashboard / "data.js").write_text(f"window.JOB_SEARCH_DATA = {serialized};\n", encoding="utf-8")
    data_dir = dashboard / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "applications.json").write_text(serialized, encoding="utf-8")
    return dashboard / "index.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path, help="directory to receive the standalone demo dashboard")
    parser.add_argument("--generated-at", help="override the display timestamp (ISO 8601 with offset)")
    args = parser.parse_args()
    if args.generated_at:
        try:
            parsed = datetime.fromisoformat(args.generated_at)
        except ValueError as exc:
            parser.error(f"--generated-at must be ISO 8601: {exc}")
        if parsed.utcoffset() is None:
            parser.error("--generated-at must include a UTC offset")
    try:
        print(build(args.destination, args.generated_at))
    except (ValueError, OSError):
        parser.exit(2, "DEMO_BLOCKER[DESTINATION_UNAVAILABLE]: Choose a fresh writable demo directory.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
