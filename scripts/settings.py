"""Private, workspace-relative policy and immutable run snapshots (stdlib only)."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULTS = {
    "schema_version": 1,
    "mode": "apply",
    "easy_apply_only": False,
    "external_hosts": {"allow": [], "deny": []},
    "daily_cap": None,
    "per_run_cap": None,
    "per_run_minutes": None,
    "company_blocklist": [],
    "title_blocklist": [],
    "max_required_years": None,
    "retryable_statuses": ["failed", "awaiting_answer"],
    "start_mode": "recommended",
    "timezone": "UTC",
    "browser": None,
    "resume_scope": None,
    "role_rules": {},
    "follow_up_days": 14,
    "computer_use": {"enabled": False, "step_budget": 6},
}
# Required fields of the original immutable schema-1 run snapshot. New optional
# policy fields must not invalidate old snapshots or change their saved hashes.
FROZEN_V1_REQUIRED = frozenset({
    "schema_version", "mode", "easy_apply_only", "external_hosts", "daily_cap",
    "per_run_cap", "per_run_minutes", "company_blocklist", "title_blocklist",
    "max_required_years", "retryable_statuses", "start_mode", "timezone",
    "browser", "resume_scope", "role_rules",
})
_RETRYABLE = {"discovered", "preparing", "awaiting_answer", "ready", "failed",
              "skipped", "saved", "dry_run_complete", "needs_review"}


class SettingsError(ValueError):
    """Invalid configuration or a policy refusal, safe for CLI handling."""


def _root(root):
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise SettingsError("Workspace does not exist; select or initialize a workspace first")
    return path


def _path(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise SettingsError("Workspace artifact escapes the selected workspace; remove the symlink")
    return path


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SettingsError("Duplicate JSON key; remove the duplicate setting")
        result[key] = value
    return result


def _read(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: _invalid("JSON", "finite numbers"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"Cannot read {label}; provide valid UTF-8 JSON") from exc


def _invalid(key, expected):
    raise SettingsError(f"settings.json: {key} must be {expected}")


def _strings(value, key):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        _invalid(key, "an array of non-empty strings")


def _host(value):
    if not isinstance(value, str) or not value or value != value.strip():
        _invalid("external_hosts", "bare DNS hostnames (no URL, wildcard, path or port)")
    try:
        host = value.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        _invalid("external_hosts", "valid DNS hostnames")
    if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                              for p in host.split(".")):
        _invalid("external_hosts", "bare DNS hostnames (no URL, wildcard, path or port)")
    return host


def _url_host(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        parsed.port  # Reject malformed ports even though host policy ignores the port.
        if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            return None
        return _host(parsed.hostname)
    except (ValueError, TypeError, SettingsError):
        return None


def _linkedin(host):
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _validate(raw):
    if not isinstance(raw, dict):
        _invalid("root", "a JSON object")
    if set(raw) - set(DEFAULTS):
        raise SettingsError("settings.json: unknown keys; use the schema in settings.example.json")
    result = copy.deepcopy(DEFAULTS)
    result.update(copy.deepcopy(raw))
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        _invalid("schema_version", "1 (supported schema)")
    if result["mode"] not in ("apply", "save_only", "dry_run"):
        _invalid("mode", "apply, save_only or dry_run")
    if type(result["easy_apply_only"]) is not bool:
        _invalid("easy_apply_only", "a boolean")
    computer = result["computer_use"]
    if not isinstance(computer, dict) or set(computer) - {"enabled", "step_budget"}:
        _invalid("computer_use", "an object with enabled/step_budget only")
    computer = {"enabled": False, "step_budget": 6, **computer}
    if type(computer["enabled"]) is not bool:
        _invalid("computer_use.enabled", "a boolean")
    if type(computer["step_budget"]) is not int or computer["step_budget"] < 0:
        _invalid("computer_use.step_budget", "a non-negative integer")
    result["computer_use"] = computer
    for key in ("daily_cap", "per_run_cap", "per_run_minutes", "max_required_years", "follow_up_days"):
        value = result[key]
        if value is not None and (type(value) is not int or value < 0):
            _invalid(key, "a non-negative integer or null (unlimited)")
    if result["follow_up_days"] is None:
        _invalid("follow_up_days", "a non-negative integer")
    hosts = result["external_hosts"]
    if not isinstance(hosts, dict) or set(hosts) - {"allow", "deny"}:
        _invalid("external_hosts", "an object with allow/deny arrays")
    hosts = {"allow": [], "deny": [], **hosts}
    for key in hosts:
        _strings(hosts[key], f"external_hosts.{key}")
        for host in hosts[key]:
            _host(host)
    result["external_hosts"] = hosts
    for key in ("company_blocklist", "title_blocklist"):
        _strings(result[key], key)
        for pattern in result[key]:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise SettingsError(f"settings.json: {key} contains an invalid regular expression") from exc
    _strings(result["retryable_statuses"], "retryable_statuses")
    if set(result["retryable_statuses"]) - _RETRYABLE:
        _invalid("retryable_statuses", "non-terminal statuses; confirmed/uncertain submissions require reconciliation")
    start = result["start_mode"]
    if not isinstance(start, str) or (start not in {"recommended", "saved"} and not (
            _url_host(start) and _linkedin(_url_host(start)) and
            urlsplit(start).scheme == "https" and urlsplit(start).path.startswith("/jobs/search"))):
        _invalid("start_mode", "recommended, saved or an HTTPS LinkedIn jobs search URL")
    if not isinstance(result["timezone"], str):
        _invalid("timezone", "an IANA timezone such as UTC")
    try:
        ZoneInfo(result["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SettingsError("settings.json: timezone is unavailable; choose an installed IANA timezone such as UTC") from exc
    for key in ("browser", "resume_scope"):
        value = result[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            _invalid(key, "a non-empty string or null")
    if not isinstance(result["role_rules"], dict):
        _invalid("role_rules", "a JSON object (interpreted by the role-policy integration)")
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise SettingsError("settings.json: values must be finite JSON data") from exc
    return result


def validate_settings(raw):
    """Validate a policy object and return an independent, default-filled copy."""
    return _validate(raw)


def load_settings(root):
    """Read only root/settings.json; missing file in an existing root uses defaults."""
    path = _path(_root(root), "settings.json")
    return _validate(_read(path, "settings.json") if path.exists() else {})


def settings_hash(settings):
    """SHA-256 of the complete validated snapshot, independent of key order/root."""
    payload = json.dumps(_validate(settings), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _aware(value, label="now"):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SettingsError(f"{label} requires a timezone-aware datetime")
    return value


def _timestamp(value, label):
    try:
        return _aware(datetime.fromisoformat(value), label)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{label} requires an ISO timestamp with UTC offset") from exc


def _run_path(root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise SettingsError("run_id must contain 1–128 letters, digits, underscores or hyphens")
    return _path(root, f"applications/runs/{run_id}/run.json")


def _deadline(start, settings):
    minutes = settings["per_run_minutes"]
    if minutes is None:
        return None
    try:
        return (start.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(
            ZoneInfo(settings["timezone"]))
    except OverflowError as exc:
        raise SettingsError("per_run_minutes exceeds the supported datetime range") from exc


def run_start(root, run_id, now):
    """Exclusively publish a complete run snapshot; an existing ID is never reused."""
    root = _root(root)
    settings = load_settings(root)
    start = _aware(now).astimezone(ZoneInfo(settings["timezone"]))
    deadline = _deadline(start, settings)
    record = {"schema_version": 1, "run_id": run_id, "started_at": start.isoformat(),
              "deadline": deadline.isoformat() if deadline else None,
              "settings": settings, "settings_hash": settings_hash(settings)}
    path = _run_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".run-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic publication without replacement, including concurrent starts.
    except FileExistsError as exc:
        raise SettingsError("Run already exists; use load_run to resume or choose a new run_id") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record


def load_run(root, run_id):
    """Verify a snapshot and deadline without consulting mutable current settings."""
    record = _read(_run_path(_root(root), run_id), "run.json; start the run first")
    if not isinstance(record, dict) or set(record) != {
            "schema_version", "run_id", "started_at", "deadline", "settings", "settings_hash"}:
        raise SettingsError("Invalid run.json schema; restore the original run snapshot")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1 or record["run_id"] != run_id:
        raise SettingsError("Invalid run.json identity/schema")
    saved = record["settings"]
    settings = _validate(saved)
    raw_hash = hashlib.sha256(json.dumps(saved, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    if not FROZEN_V1_REQUIRED.issubset(saved) or raw_hash != record["settings_hash"]:
        raise SettingsError("Run settings snapshot/hash mismatch; restore the original run snapshot")
    start = _timestamp(record["started_at"], "run.started_at")
    expected = _deadline(start, settings)
    if record["deadline"] != (expected.isoformat() if expected else None):
        raise SettingsError("Run deadline mismatch; restore the original run snapshot")
    return record


def eligibility(data, settings):
    """Return None or a stable rejection reason using only listing/header fields."""
    settings = _validate(settings)
    if not isinstance(data, dict):
        raise SettingsError("Listing header must be an object")
    url = data.get("application_url") or data.get("listing_url") or data.get("url")
    host = _url_host(url) if url else None
    external = (data.get("easy_apply") is False or data.get("is_easy_apply") is False or
                data.get("apply_type") == "external" or (host is not None and not _linkedin(host)))
    if settings["easy_apply_only"] and external:
        return "skipped:external_apply"
    for key, value in (("company_blocklist", data.get("company", "")),
                       ("title_blocklist", data.get("role") or data.get("title", ""))):
        if any(re.search(pattern, str(value or ""), re.IGNORECASE) for pattern in settings[key]):
            return "skipped:blocklist"
    if url and host is None:
        return "skipped:invalid_application_url"
    if host and not _linkedin(host):
        def matches(rule):
            rule = _host(rule)
            return host == rule or host.endswith("." + rule)
        hosts = settings["external_hosts"]
        if any(matches(x) for x in hosts["deny"]) or (hosts["allow"] and not any(matches(x) for x in hosts["allow"])):
            return "skipped:external_host"
    years = data.get("required_years")
    if years is not None:
        if type(years) not in (int, float) or not math.isfinite(years) or years < 0:
            raise SettingsError("required_years must be a non-negative finite number, or absent")
        if settings["max_required_years"] is not None and years > settings["max_required_years"]:
            return "skipped:required_years"
    return None


def _normal(value):
    return " ".join(str(value or "").casefold().split())


def _canonical(url):
    if not _url_host(url):
        return None
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_") and
             k not in {"trk", "trackingId", "refId"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"),
                       urlencode(sorted(query)), ""))


def _identity_keys(record):
    keys = []
    if record.get("application_id"):
        keys.append(("id", record["application_id"]))
    if record.get("platform_job_id"):
        keys.append(("platform", _normal(record.get("platform")), str(record["platform_job_id"])))
    if record.get("employer_requisition_id") and record.get("company"):
        keys.append(("requisition", _normal(record["company"]), str(record["employer_requisition_id"])))
    for key in ("canonical_listing_url", "listing_url"):
        canonical = _canonical(record.get(key))
        if canonical:
            keys.append(("url", canonical))
    if all(record.get(x) for x in ("company", "role", "location")):
        keys.append(("role", *(_normal(record[x]) for x in ("company", "role", "location"))))
    if not keys:
        raise SettingsError("Application record has no stable identity; repair it before submitting")
    return keys


def _record_run(record):
    prepared = record.get("prepared") or {}
    if not isinstance(prepared, dict):
        raise SettingsError("Application prepared snapshot must be an object")
    a, b = record.get("run_id"), prepared.get("run_id")
    if a and b and a != b:
        raise SettingsError("Application run_id disagrees with its prepared snapshot; prepare again")
    return a or b


def _confirmed(record):
    submissions = record.get("submissions", [])
    if not isinstance(submissions, list) or any(not isinstance(x, dict) for x in submissions):
        raise SettingsError("Application submissions must be an array of objects")
    result = []
    for saved in submissions:
        if saved.get("submitted_at") and saved.get("status", "submitted") == "submitted":
            # Never attribute old attempts to a later top-level run assignment.
            result.append((_timestamp(saved["submitted_at"], "submitted_at"), saved.get("run_id")))
    if record.get("submitted_at"):
        at = _timestamp(record["submitted_at"], "submitted_at")
        if not any(t == at for t, _ in result):
            result.append((at, record.get("run_id")))
    elif record.get("status") == "submitted" and not result:
        raise SettingsError("Confirmed application lacks submitted_at; repair it before submitting")
    return result


def _groups(root):
    """Union all dedupe identities before counting, including cross-post bridges."""
    parents, events, identities = [], [], {}
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for path in sorted(_path(root, "applications/records").glob("*/record.json")):
        record = _read(_path(root, path.relative_to(root)), "application record.json")
        if not isinstance(record, dict):
            raise SettingsError("Application record.json must be an object")
        i = len(parents)
        parents.append(i)
        events.append(_confirmed(record))
        for key in _identity_keys(record):
            if key in identities:
                parents[find(i)] = find(identities[key])
            identities[key] = i
    groups = {}
    for i, saved in enumerate(events):
        groups.setdefault(find(i), []).extend(saved)
    return list(groups.values())


def check_submission(root, record, now):
    """Settings gate to compose with resume/answer/identity preflight under writer lock."""
    root, now = _root(root), _aware(now)
    if not isinstance(record, dict) or record.get("status") != "ready":
        raise SettingsError("Only a ready application may pass settings preflight")
    current = load_settings(root)
    run_id = _record_run(record)
    run = load_run(root, run_id) if run_id is not None else None
    policies = [current] + ([run["settings"]] if run else [])
    groups = _groups(root)
    counts = []
    for settings in policies:
        if settings["mode"] != "apply":
            raise SettingsError(f"mode:{settings['mode']}: Submit is disabled")
        reason = eligibility(record, settings)
        if reason:
            raise SettingsError(reason)
        zone = ZoneInfo(settings["timezone"])
        day = now.astimezone(zone).date()
        daily = sum(any(at.astimezone(zone).date() == day for at, _ in saved) for saved in groups)
        per_run = sum(any(identity == run_id for _, identity in saved) for saved in groups) if run else 0
        counts.append((daily, per_run))
        if settings["daily_cap"] is not None and daily >= settings["daily_cap"]:
            raise SettingsError("cap:daily: unique confirmed daily application limit reached")
        if not run and any(settings[key] is not None for key in ("per_run_cap", "per_run_minutes")):
            raise SettingsError("run:required: start a run and attach run_id before preparing")
        if run:
            start = _timestamp(run["started_at"], "run.started_at")
            if now < start:
                raise SettingsError("run:not_started: now precedes the frozen run start")
            if settings["per_run_cap"] is not None and per_run >= settings["per_run_cap"]:
                raise SettingsError("cap:per_run: unique confirmed run application limit reached")
            deadline = _deadline(start, settings)
            if deadline is not None and now >= deadline:
                raise SettingsError("cap:deadline: run time limit reached")
    return {"ready": True, "run_id": run_id, "settings_hash": settings_hash(current),
            "run_settings_hash": run["settings_hash"] if run else None,
            "daily_count": counts[0][0], "run_count": counts[0][1]}
