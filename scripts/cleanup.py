#!/usr/bin/env python3
"""Run-scoped browser tab cleanup and application run summaries (stdlib only)."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


class CleanupError(ValueError):
    """Invalid run, tab, or workspace data."""


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _run_dir(root, run_id):
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise CleanupError("run_id must contain 1–128 letters, digits, underscores or hyphens")
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise CleanupError("Workspace does not exist")
    path = base / "applications" / "runs" / run_id
    if not path.resolve().is_relative_to(base):
        raise CleanupError("Run path escapes the workspace")
    return path


def _id(value, name):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,17}", value):
        return int(value)
    raise CleanupError(f"{name} must be a positive numeric browser ID")


def _url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise CleanupError("Tab URL must be a valid HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        parsed.port
    except ValueError as exc:
        raise CleanupError("Tab URL must be a valid HTTP(S) URL") from exc
    return value


def _host(url):
    try:
        hostname = urlsplit(_url(url)).hostname
        return hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, CleanupError):
        return None


def _allowed(host, allowed_hosts):
    for allowed in allowed_hosts:
        if not isinstance(allowed, str) or not allowed.strip() or "://" in allowed or "/" in allowed:
            raise CleanupError("allowed_hosts must contain bare hostnames")
        try:
            normalized = allowed.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise CleanupError("allowed_hosts must contain valid hostnames") from exc
        if host == normalized or host.endswith("." + normalized):
            return True
    return False


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cleanup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanupError(f"Cannot read {path.name}; provide valid UTF-8 JSON") from exc


def register_tab(root, run_id, window_id, tab_id, url, application_id, now):
    """Persist a tab opened by this run; unregistered tabs are never candidates."""
    directory = _run_dir(root, run_id)
    window_id, tab_id = _id(window_id, "window_id"), _id(tab_id, "tab_id")
    url = _url(url)
    if not isinstance(application_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", application_id):
        raise CleanupError("application_id must be a safe application identifier")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CleanupError("now must be a timezone-aware datetime")
    path = directory / "tabs.json"
    data = _read_json(path, {"schema_version": 1, "run_id": run_id, "tabs": []})
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("run_id") != run_id or not isinstance(data.get("tabs"), list):
        raise CleanupError("Invalid tabs.json schema")
    item = {"window_id": window_id, "tab_id": tab_id, "url": url,
            "application_id": application_id, "opened_at": now.isoformat()}
    for existing in data["tabs"]:
        if not isinstance(existing, dict):
            raise CleanupError("Invalid registered tab entry")
        if (existing.get("window_id"), existing.get("tab_id")) == (window_id, tab_id):
            if existing == item:
                return item
            raise CleanupError("Browser tab ID is already registered with different details")
    data["tabs"].append(item)
    _atomic_json(path, data)
    return item


def close_candidates(registered, current_tabs, records, allowed_hosts):
    """Return close/skip decisions. Only exact registered window/tab IDs qualify."""
    if not isinstance(registered, list) or not isinstance(current_tabs, list) or not isinstance(records, list):
        raise CleanupError("registered, current_tabs, and records must be lists")
    by_id = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("application_id"), str):
            continue
        by_id[record["application_id"]] = record
    current = {}
    for tab in current_tabs:
        if not isinstance(tab, dict):
            raise CleanupError("Current tab entries must be objects")
        key = (_id(tab.get("window_id"), "window_id"), _id(tab.get("tab_id"), "tab_id"))
        if key in current:
            raise CleanupError("Duplicate current browser tab IDs")
        current[key] = {**tab, "window_id": key[0], "tab_id": key[1], "url": str(tab.get("url", ""))}
    seen = set()
    decisions = []
    for item in registered:
        if not isinstance(item, dict):
            raise CleanupError("Registered tab entries must be objects")
        key = (_id(item.get("window_id"), "window_id"), _id(item.get("tab_id"), "tab_id"))
        if key in seen:
            raise CleanupError("Duplicate registered browser tab IDs")
        seen.add(key)
        registered_url = _url(item.get("url"))
        app_id = item.get("application_id")
        record = by_id.get(app_id, {})
        current_tab = current.get(key)
        row = {"window_id": key[0], "tab_id": key[1], "url": registered_url,
               "application_id": app_id}
        if not record:
            row.update(action="retain", reason="application_record_missing")
        elif record.get("status") == "submission_uncertain":
            row.update(action="retain", reason="submission_uncertain")
        elif current_tab is None:
            row.update(action="skip", reason="tab_not_open")
        else:
            current_url = current_tab["url"]
            if not _allowed(_host(current_url) or "", allowed_hosts):
                row.update(action="retain", reason="host_moved_outside_allowlist", current_url=current_url)
            elif current_url != registered_url:
                row.update(action="skip", reason="url_changed", current_url=current_url)
            else:
                row.update(action="close", reason="registered_tab", current_url=current_url)
        decisions.append(row)
    return decisions


def _records_for_run(root, run_id):
    result = []
    directory = Path(root) / "applications" / "records"
    if not directory.exists():
        return result
    tabs = _read_json(Path(root) / "applications/runs" / run_id / "tabs.json", {"tabs": []})
    registered = {tab.get("application_id") for tab in tabs.get("tabs", [])}
    for path in sorted(directory.glob("*/record.json")):
        record = _read_json(path, None)
        if isinstance(record, dict) and (record.get("run_id") == run_id or record.get("application_id") in registered):
            result.append(record)
    return result


def _entry(record):
    return {"application_id": record.get("application_id"), "company": record.get("company"),
            "role": record.get("role") or record.get("title"), "status": record.get("status"),
            "reason": record.get("stop_reason") or record.get("reason"),
            "url": record.get("application_url") or record.get("listing_url") or record.get("url")}


def run_summary(root, run_id, now):
    """Write a stable JSON and Markdown summary, even for empty or stopped runs."""
    directory = _run_dir(root, run_id)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CleanupError("now must be a timezone-aware datetime")
    records = _records_for_run(root, run_id)
    counts = {"applied": 0, "skipped": 0, "awaiting_answers": 0, "failures": 0}
    skipped_reasons = {}
    uncertain = []
    entries = []
    for record in records:
        row = _entry(record)
        status = record.get("status")
        confirmed_in_run = any(s.get("run_id", record.get("run_id")) == run_id for s in record.get("submissions", []))
        if confirmed_in_run or (status == "submitted" and record.get("run_id") == run_id):
            counts["applied"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
            reason = str(row["reason"] or "unspecified")
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        elif status == "awaiting_answer":
            counts["awaiting_answers"] += 1
        elif status == "failed":
            counts["failures"] += 1
        elif status == "submission_uncertain":
            uncertain.append({**row, "reconciliation_required": True})
        entries.append(row)
    tabs_doc = _read_json(directory / "tabs.json", {"tabs": []})
    tab_by_app = {t.get("application_id"): t for t in tabs_doc.get("tabs", []) if isinstance(t, dict)}
    uncertain_tabs = []
    for item in uncertain:
        tab = tab_by_app.get(item.get("application_id"))
        if tab:
            uncertain_tabs.append({"application_id": item["application_id"], "window_id": tab.get("window_id"),
                                   "tab_id": tab.get("tab_id"), "url": tab.get("url"),
                                   "reason": "submission_uncertain"})
    summary = {"schema_version": 1, "run_id": run_id, "generated_at": now.isoformat(),
               "counts": counts, "skipped_reasons": skipped_reasons, "entries": entries,
               "awaiting_answers": [e for e in entries if e["status"] == "awaiting_answer"],
               "failures": [e for e in entries if e["status"] == "failed"],
               "uncertain_submissions": uncertain, "uncertain_tabs_retained": uncertain_tabs,
               "stop_reason": _read_json(directory / "safety.json", {}).get("stop_reason")}
    _atomic_json(directory / "summary.json", summary)
    lines = [f"# Run {run_id} summary", "", f"Generated: {now.isoformat()}", "",
             "## Counts", "", f"- Applied: {counts['applied']}", f"- Skipped: {counts['skipped']}",
             f"- Awaiting answers: {counts['awaiting_answers']}", f"- Failures: {counts['failures']}", "",
             "## Skipped by reason", ""]
    if skipped_reasons:
        lines.extend(f"- {reason}: {count}" for reason, count in sorted(skipped_reasons.items()))
    else:
        lines.append("- None")
    lines += ["", "## Applications", ""]
    if entries:
        for entry in entries:
            label = " — ".join(str(v) for v in (entry["company"], entry["role"]) if v)
            link = f" ([listing]({entry['url']}))" if entry["url"] else ""
            reason = f" — {entry['reason']}" if entry["reason"] else ""
            lines.append(f"- {entry['status']}: {label or entry['application_id']}{reason}{link}")
    else:
        lines.append("- No applications recorded")
    lines += ["", "## Submission reconciliation", ""]
    if uncertain:
        lines.append("The following submissions need evidence-based reconciliation. Their registered tabs remain open:")
        for item in uncertain:
            tab = tab_by_app.get(item["application_id"], {})
            lines.append(f"- {item['application_id']}: {item.get('url') or tab.get('url') or 'no URL'}")
    else:
        lines.append("- None")
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


class BraveAppleScriptAdapter:
    """Small osascript bridge. Constructed only for explicit execute mode."""
    def _run(self, script):
        completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True)
        return completed.stdout.strip()

    def enumerate_tabs(self):
        script = '''tell application "Brave Browser"
set out to ""
repeat with w in windows
 repeat with t in tabs of w
  set out to out & (id of w as text) & tab & (id of t as text) & tab & (URL of t) & linefeed
 end repeat
end repeat
return out
end tell'''
        raw = self._run(script)
        tabs = []
        for line in raw.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                tabs.append({"window_id": _id(parts[0], "window_id"), "tab_id": _id(parts[1], "tab_id"), "url": parts[2]})
        return tabs

    def close_tab(self, window_id, tab_id):
        window_id, tab_id = _id(window_id, "window_id"), _id(tab_id, "tab_id")
        self._run(f'''tell application "Brave Browser" to close (first tab of window id {window_id} whose id is {tab_id})''')


def _cleanup_run(root, run_id, now, adapter=None, execute=False, export_callback=None):
    """Plan cleanup by default; execute closes only reverified registered tabs."""
    directory = _run_dir(root, run_id)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CleanupError("now must be a timezone-aware datetime")
    tabs_doc = _read_json(directory / "tabs.json", {"schema_version": 1, "run_id": run_id, "tabs": []})
    if not isinstance(tabs_doc, dict) or tabs_doc.get("run_id") != run_id or not isinstance(tabs_doc.get("tabs"), list):
        raise CleanupError("Invalid tabs.json schema")
    records = [_read_json(path, None) for path in sorted((Path(root) / "applications/records").glob("*/record.json"))]
    if execute:
        adapter = adapter or BraveAppleScriptAdapter()
        current = adapter.enumerate_tabs()
    else:
        current = []
    # In plan-only mode, registered entries are candidates based on their saved URL; no live browser is read.
    if execute:
        decisions = close_candidates(tabs_doc["tabs"], current, records, _allowed_hosts(root, run_id))
    else:
        decisions = close_candidates(tabs_doc["tabs"],
            [{"window_id": t["window_id"], "tab_id": t["tab_id"], "url": t["url"]} for t in tabs_doc["tabs"]],
            records, _allowed_hosts(root, run_id))
        for decision in decisions:
            if decision["action"] == "close":
                decision["action"] = "plan"
    closed, errors = [], []
    if execute:
        for decision in decisions:
            if decision["action"] != "close":
                continue
            key = (decision["window_id"], decision["tab_id"])
            # Fresh enumeration immediately before each close prevents stale-list tab reuse.
            latest = adapter.enumerate_tabs()
            match = next((t for t in latest if (_id(t.get("window_id"), "window_id"), _id(t.get("tab_id"), "tab_id")) == key), None)
            if not match or match.get("url") != decision["url"]:
                decision.update(action="skip", reason="url_changed_before_close")
                continue
            try:
                adapter.close_tab(*key)
                decision["action"] = "closed"
                closed.append({"window_id": key[0], "tab_id": key[1], "application_id": decision["application_id"]})
            except Exception as exc:
                decision.update(action="failed", reason="close_error", error_type=type(exc).__name__)
                errors.append({"window_id": key[0], "tab_id": key[1], "error_type": type(exc).__name__})
    result = {"schema_version": 1, "run_id": run_id, "generated_at": now.isoformat(),
              "mode": "execute" if execute else "plan", "decisions": decisions,
              "closed": closed, "failures": errors}
    _atomic_json(directory / "cleanup.json", result)
    if export_callback is not None:
        export_callback()
    summary = run_summary(root, run_id, now)
    result["summary"] = summary
    return result


def cleanup_run(root, run_id, now, adapter=None, execute=False, export_callback=None):
    try:
        return _cleanup_run(root, run_id, now, adapter, execute, export_callback)
    except Exception:
        run_summary(root, run_id, now)
        raise


def _allowed_hosts(root, run_id):
    run_path = Path(root) / "applications" / "runs" / run_id / "run.json"
    run = _read_json(run_path, {})
    settings = run.get("settings", {}) if isinstance(run, dict) else {}
    external = settings.get("external_hosts", {}) if isinstance(settings, dict) else {}
    allowed = external.get("allow", []) if isinstance(external, dict) else []
    return ["linkedin.com", *allowed]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true", help="Close verified registered Brave tabs")
    args = parser.parse_args(argv)
    result = cleanup_run(args.workspace, args.run_id, datetime.now().astimezone(), execute=args.execute)
    print(json.dumps({k: v for k, v in result.items() if k != "summary"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
