"""Durable, local-only run stops. Importing this module never notifies or browses."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo


SENTINEL = "applications/SAFETY_STOP"
PENDING = "applications/SAFETY_STOP_PENDING"
STOP = "applications/STOP"
_REASONS = {"manual_stop", "account_safety"}
_KINDS = {"captcha", "challenge", "unusual_activity", "sign_in", "rate_limit", "restriction"}


class SafetyError(ValueError):
    """Invalid or unreadable safety state; work must remain blocked."""


class SafetyBlocked(SafetyError):
    def __init__(self, record):
        self.record = record
        self.stop_reason = record["stop_reason"]
        super().__init__(f"BLOCKED: {self.stop_reason}; explicit resume-stop evidence required")


class EvidenceRequired(SafetyBlocked):
    """The stop is durable, but its browser screenshot is still missing."""

    def __init__(self, record):
        super().__init__(record)
        self.args = ("BLOCKED: account_safety; browser screenshot evidence required",)


@dataclass(frozen=True)
class Detection:
    kind: str
    signal: str
    stop_reason: str = "account_safety"


def detect(page_text="", url=""):
    """Return a typed detection or None; never retain page text or URL tokens.

    The adapter supplies the current rendered page, not a job description alone.
    A normal navigation-bar Sign in link is insufficient to declare a login wall.
    """
    if not isinstance(page_text, str) or not isinstance(url, str):
        raise SafetyError("Detector requires text and URL strings")
    text = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", page_text)).lower().split())
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        path = ""
    routes = (
        ("captcha", r"(?:^|/)(?:captcha|recaptcha)(?:/|$)"),
        ("challenge", r"(?:^|/)(?:checkpoint|challenge|security-verification)(?:/|$)"),
        ("sign_in", r"(?:^|/)(?:login|signin|sign-in|authwall)(?:/|$)"),
        ("restriction", r"(?:^|/)(?:restricted|account-restricted|account-suspended)(?:/|$)"),
    )
    for kind, pattern in routes:
        if re.search(pattern, path):
            return Detection(kind, "url_path")
    patterns = (
        ("captcha", r"\b(?:captcha|recaptcha|hcaptcha)\b"),
        ("challenge", r"\b(?:verify (?:that )?you(?:'re|’re| are) (?:a )?human|verify your identity|security (?:check|verification)|complete (?:this|the) challenge|checking your browser)\b"),
        ("unusual_activity", r"\b(?:unusual activity|suspicious activity|automated (?:activity|behavior|behaviour)|unusualactivity)\b"),
        ("restriction", r"\b(?:(?:your )?account (?:has been |is |was |temporarily )*(?:restricted|suspended|locked|disabled)|access (?:to your account )?(?:is |has been )?(?:restricted|denied)|restriction notice)\b"),
        ("rate_limit", r"\b(?:too many requests|rate[ -]?limit(?:ed|ing| exceeded)?|request limit (?:reached|exceeded)|try again (?:later|in \d+ (?:minutes?|hours?)))\b"),
        ("sign_in", r"^(?:sign in|log in|login)(?:\b|$)|\b(?:sign in|log in|login) (?:to continue|to (?:view|see|access)|required)|\bsession (?:has )?expired\b"),
    )
    for kind, pattern in patterns:
        if re.search(pattern, text):
            return Detection(kind, "page_text")
    return None


def _root(root):
    result = Path(root).expanduser().resolve()
    if not result.is_dir():
        raise SafetyError("Safety workspace must already exist")
    return result


def _path(root, relative):
    path = root / relative
    if path.resolve() != path:
        raise SafetyError("Safety artifact must not follow workspace symlinks")
    return path


def _run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise SafetyError("A safe, non-empty run_id is required")
    return value


def _time(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise SafetyError("now requires a timezone-aware datetime")
    return now.isoformat()


def _exists(path):
    # A dangling STOP/sentinel symlink must also block work.
    return os.path.lexists(path)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".safety-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate safety key")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, ValueError, UnicodeError) as exc:
        raise SafetyError("Unreadable safety state; explicit reconciliation required") from exc
    if not isinstance(value, dict):
        raise SafetyError("Invalid safety state; explicit reconciliation required")
    return value


@contextmanager
def _lock(root):
    path = _path(root, "applications/safety/.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _stop_path(root, stop_id):
    if not isinstance(stop_id, str) or not re.fullmatch(r"[a-f0-9]{32}", stop_id):
        raise SafetyError("Invalid safety stop identity")
    return _path(root, f"applications/safety/stops/{stop_id}.json")


def _load_stop(root, path):
    record = _read(path)
    try:
        valid = (type(record["schema_version"]) is int and record["schema_version"] == 1
                 and record["stop_reason"] in _REASONS
                 and record["status"] in {"stopped", "resolved"}
                 and _stop_path(root, record["stop_id"]) == path
                 and _run_id(record["run_id"]) == record["run_id"]
                 and record["notification"]["status"] in
                 {"pending", "attempting", "delivered", "failed"})
        _time(datetime.fromisoformat(record["stopped_at"]))
        evidence = record["evidence"]
        if evidence is not None:
            # Do not require the original image to remain mounted just to block work.
            reference = Path(evidence["screenshot"])
            valid = (valid and not reference.is_absolute() and ".." not in reference.parts
                     and reference.parts[0] == "applications"
                     and evidence.get("kind") in _KINDS | {None})
        notification = record["notification"]
        if notification["status"] != "pending":
            _time(datetime.fromisoformat(notification["attempted_at"]))
        if notification["status"] == "delivered":
            _time(datetime.fromisoformat(notification["delivered_at"]))
        if record["status"] == "resolved":
            resolution = record["resolution"]
            valid = valid and resolution["command"] in {"resume-stop", "reconcile"}
            valid = valid and all(isinstance(resolution[key], str) and resolution[key].strip()
                                  for key in ("operator", "note"))
            _time(datetime.fromisoformat(resolution["at"]))
    except (KeyError, TypeError, ValueError, IndexError):
        valid = False
    if not valid:
        raise SafetyError("Invalid safety stop record; explicit reconciliation required")
    return record


def _active(root):
    sentinel = _path(root, SENTINEL)
    directory = _path(root, "applications/safety/stops")
    # The ledger recovers a crash between committing a stop and its sentinel.
    unresolved = []
    for path in sorted(directory.glob("*.json")):
        record = _load_stop(root, _path(root, path.relative_to(root)))
        if record["status"] == "stopped":
            unresolved.append(record)
    if _exists(sentinel):
        marker = _read(sentinel)
        saved = _load_stop(root, _stop_path(root, marker.get("stop_id")))
        if saved["status"] == "stopped":
            return saved
        # A resolved ledger with a remaining sentinel is a safe interrupted resume.
        sentinel.unlink()
        _sync(sentinel.parent)
    if unresolved:
        _write(sentinel, {"stop_id": unresolved[0]["stop_id"]})
        return unresolved[0]
    return None


def _screenshot(root, evidence):
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or not isinstance(evidence.get("screenshot"), str):
        raise SafetyError("Account-safety evidence requires a browser screenshot reference")
    path = Path(evidence["screenshot"])
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    private = root / "applications"
    if (not path.is_relative_to(private) or not path.is_file()
            or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}):
        raise SafetyError("Browser screenshot must be an existing private applications image")
    kind = evidence.get("kind")
    if kind is not None and kind not in _KINDS:
        raise SafetyError("Unknown account-safety detection kind")
    # Do not store arbitrary page text, URLs, image bytes, or adapter metadata.
    return {"screenshot": path.relative_to(root).as_posix(), "kind": kind}


def _run_record(root, run_id, record, at, command=None):
    path = _path(root, f"applications/runs/{run_id}/safety.json")
    if not _exists(path):
        _write(path, {"schema_version": 1, "run_id": run_id, "status": "stopped",
                      "stop_id": record["stop_id"], "stop_reason": record["stop_reason"],
                      "stopped_at": at, "command": command})


def _stop(root, run_id, reason, at, evidence=None, command=None):
    record = _active(root)
    if record is None:
        record = {"schema_version": 1, "stop_id": uuid4().hex, "run_id": run_id,
                  "status": "stopped", "stop_reason": reason, "stopped_at": at,
                  "evidence": None, "notification": {"status": "pending"}}
        _write(_stop_path(root, record["stop_id"]), record)
        _write(_path(root, SENTINEL), {"stop_id": record["stop_id"]})
    _run_record(root, run_id, record, at, command)
    if reason == "account_safety":
        # Persist first, even when the browser cannot capture its evidence.
        # A later successful capture may fill evidence without replacing the stop.
        record["account_safety"] = True
        try:
            screenshot = _screenshot(root, evidence)
        except SafetyError as exc:
            _write(_stop_path(root, record["stop_id"]), record)
            raise EvidenceRequired(record) from exc
        if screenshot and record["evidence"] is None:
            record["evidence"] = screenshot
        _write(_stop_path(root, record["stop_id"]), record)
    return record


def _notify(root, record, at, callback):
    if callback is None or record["notification"]["status"] != "pending":
        return record
    # Reserve before external delivery. Never replay an ambiguous attempt.
    record["notification"] = {"status": "attempting", "attempted_at": at}
    path = _stop_path(root, record["stop_id"])
    _write(path, record)
    payload = {key: record[key] for key in ("stop_id", "run_id", "stop_reason")}
    try:
        callback(payload)
    except Exception as exc:
        record["notification"].update(status="failed", error_type=type(exc).__name__)
    else:
        record["notification"].update(status="delivered", delivered_at=at)
    _write(path, record)
    return record


def stop_run(root, run_id, reason, now, evidence=None, notify=None):
    """Persist the stop once; optionally deliver through an explicit callback.

    account_safety requires {"screenshot": private image ref, "kind": optional kind}.
    Missing evidence raises EvidenceRequired *after* durable blocking/notification.
    """
    root, run_id, at = _root(root), _run_id(run_id), _time(now)
    if reason not in _REASONS:
        raise SafetyError("stop reason must be manual_stop or account_safety")
    with _lock(root):
        try:
            record = _stop(root, run_id, reason, at, evidence)
        except EvidenceRequired as exc:
            _notify(root, exc.record, at, notify)
            raise
        record = _notify(root, record, at, notify)
        if record.get("account_safety") and record["evidence"] is None:
            raise EvidenceRequired(record)
        return record


def guard(root, run_id, now, command):
    """Return None to proceed; otherwise persist refusal then raise SafetyBlocked.

    Call before new work, including seen, capture and preflight. No commands are
    whitelisted here; reconciliation and historical logging bypass this function.
    """
    root, run_id, at = _root(root), _run_id(run_id), _time(now)
    if not isinstance(command, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", command):
        raise SafetyError("command requires a public command name, without arguments")
    with _lock(root):
        record = _active(root)
        if record is None and (_exists(_path(root, STOP)) or _exists(_path(root, PENDING))):
            record = _stop(root, run_id, "manual_stop", at, command=command)
        if record is not None:
            _run_record(root, run_id, record, at, command)
            raise SafetyBlocked(record)


def notify_stop(root, now, notify):
    """Deliver a pending notification explicitly, without unblocking any work."""
    root, at = _root(root), _time(now)
    with _lock(root):
        record = _active(root)
        if record is None:
            raise SafetyError("No unresolved safety stop")
        return _notify(root, record, at, notify)


def resume_stop(root, run_id, now, evidence, command):
    """Resolve one incident only for an explicit operator command and evidence.

    evidence must include operator and note. Neither removing STOP nor running
    guard with a special command grants a resume. The source stop ID must match.
    """
    root, run_id, at = _root(root), _run_id(run_id), _time(now)
    if command not in {"resume-stop", "reconcile"}:
        raise SafetyError("Explicit resume-stop or reconcile command required")
    if (not isinstance(evidence, dict) or any(not isinstance(evidence.get(key), str)
            or not evidence[key].strip() for key in ("operator", "note"))):
        raise SafetyError("Explicit operator and reconciliation note required")
    with _lock(root):
        if _exists(_path(root, STOP)):
            raise SafetyError("Remove applications/STOP before explicit reconciliation")
        record = _active(root)
        if record is None or record["run_id"] != run_id:
            raise SafetyError("Reconcile the originating stopped run")
        record["status"] = "resolved"
        record["resolution"] = {"command": command, "at": at,
                                "operator": evidence["operator"], "note": evidence["note"]}
        _write(_stop_path(root, record["stop_id"]), record)
        for relative in (SENTINEL, PENDING):
            path = _path(root, relative)
            if _exists(path):
                path.unlink()
                _sync(path.parent)
        # safety.json preserves the run's original stop; do not rewrite history.
        return record


def reconcile(root, run_id, now, evidence, command):
    return resume_stop(root, run_id, now, evidence, command)


def local_notification(record):
    """Explicit opt-in macOS notification; no page content or paths enter argv."""
    if record.get("stop_reason") not in _REASONS:
        raise SafetyError("Invalid notification reason")
    subprocess.run(["osascript", "-e",
                    'display notification "Run stopped. Review local safety state." '
                    'with title "applyd"'], check=True, capture_output=True, timeout=10)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--notify-local", action="store_true",
                        help="explicitly deliver a local macOS notification")
    sub = parser.add_subparsers(dest="action", required=True)
    gate = sub.add_parser("guard")
    gate.add_argument("command")
    stop = sub.add_parser("stop")
    stop.add_argument("reason", choices=sorted(_REASONS))
    stop.add_argument("--screenshot")
    stop.add_argument("--kind", choices=sorted(_KINDS))
    for action in ("resume-stop", "reconcile"):
        resume = sub.add_parser(action)
        resume.add_argument("--operator", required=True)
        resume.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    from settings import load_settings
    now = datetime.now(ZoneInfo(load_settings(Path(args.workspace))["timezone"]))
    callback = local_notification if args.notify_local else None
    try:
        if args.action == "guard":
            try:
                guard(args.workspace, args.run_id, now, args.command)
            except SafetyBlocked:
                if callback:
                    notify_stop(args.workspace, now, callback)
                raise
        elif args.action == "stop":
            evidence = {"screenshot": args.screenshot, "kind": args.kind} if args.screenshot else None
            record = stop_run(args.workspace, args.run_id, args.reason, now, evidence, callback)
            raise SafetyBlocked(record)
        else:
            resume_stop(args.workspace, args.run_id, now,
                        {"operator": args.operator, "note": args.note}, args.action)
    except SafetyError as exc:
        print(str(exc))
        return 2
    except OSError:
        print("BLOCKED: safety state I/O failure; explicit reconciliation required")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
