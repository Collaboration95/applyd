"""Bounded, private computer-use sessions. No browser or OS automation is provided.

Workspace policy is refreshed at every guarded adapter boundary. An adapter must
implement the safety contract in docs/computer-use.md.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol
from urllib.parse import urlsplit

import settings


class Outcome(str, Enum):
    DISABLED = "disabled"
    OFF_SCOPE = "off_scope"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SCREENSHOT_MISSING = "screenshot_missing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Action:
    """One non-submitting widget operation; never a click/key/script command."""

    kind: str
    target: str
    value: str | None = None


@dataclass(frozen=True)
class Observation:
    """Current active tab and an actual, adapter-captured private image reference."""

    tab_id: str
    url: str
    screenshot: str | None
    easy_apply: bool = False


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    reason: str
    step_count: int | None
    record_ref: str


class Adapter(Protocol):
    def observe(self) -> Observation:
        """Inspect current focus; capture an image without changing browser state."""
        ...

    def perform(self, action: Action, *, tab_id: str, origin: str,
                resume_path: Path | None) -> None:
        """Recheck scope atomically; perform only the named safe widget operation."""
        ...


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_FAILURES = {"native_file_picker", "canvas", "custom_widget"}
_WIDGET_ACTIONS = {"select_option", "set_widget_value"}
_SGT = timezone(timedelta(hours=8))


def _now():
    return datetime.now(_SGT).isoformat()


def _host(value):
    if not isinstance(value, str) or value != value.strip():
        raise ValueError
    host = value.encode("idna").decode("ascii").lower().rstrip(".")
    if len(host) > 253 or any(not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
        raise ValueError
    return host


def _origin(url):
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
        raise ValueError
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None):
        raise ValueError
    host = _host(parsed.hostname)
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{host}:{port}", host


def _matches(host, rule):
    return host == rule or host.endswith("." + rule)


def _permitted(host, policy):
    external = policy.get("external_hosts", {})
    if not isinstance(external, dict) or set(external) - {"allow", "deny"}:
        return False
    allow, deny = external.get("allow", []), external.get("deny", [])
    if not isinstance(allow, list) or not isinstance(deny, list):
        return False
    allow, deny = [_host(x) for x in allow], [_host(x) for x in deny]
    return (not _matches(host, "linkedin.com") and not policy.get("easy_apply_only", False)
            and any(_matches(host, rule) for rule in allow)
            and not any(_matches(host, rule) for rule in deny))


def effective_policy(current, frozen, initial_url):
    """Intersect validated current/frozen policies for exactly one HTTP(S) origin.

    Empty host allowlists never authorize fallback. Legacy frozen snapshots omit
    computer_use and therefore remain disabled. Inputs are never modified.
    """
    current, frozen = (settings.validate_settings(p) for p in (current, frozen))
    try:
        origin, host = _origin(initial_url)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise settings.SettingsError("Fallback requires an observed HTTP(S) origin") from exc
    policies = (current, frozen)
    return {
        "mode": "apply" if all(p["mode"] == "apply" for p in policies) else "save_only",
        "easy_apply_only": any(p["easy_apply_only"] for p in policies),
        "computer_use": {
            "enabled": all(p["computer_use"]["enabled"] for p in policies),
            "step_budget": min(p["computer_use"]["step_budget"] for p in policies),
        },
        "external_hosts": {
            "allow": [host] if all(_permitted(host, p) for p in policies) else [],
            "deny": sorted(set(current["external_hosts"]["deny"] + frozen["external_hosts"]["deny"])),
        },
        "allowed_origin": origin,
    }


def run_workspace_session(root, run_id, session_id, *, guard, clock=None, **arguments):
    """Load verified frozen/current settings and enforce both deadlines per call.

    Remaining arguments match run_session except policy/refresh_policy. guard
    must check live kill/safety, eligibility, freshness and browser ownership and
    return exactly True; it is invoked before every observe/perform. clock is an
    injectable aware-datetime function; the default reads the actual UTC clock.
    The run must already exist (settings.run_start freezes computer_use).
    """
    if "policy" in arguments or "refresh_policy" in arguments:
        raise ValueError("Workspace fallback loads its own policies")
    clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def refresh():
        current = settings.load_settings(root)
        run = settings.load_run(root, run_id)
        frozen = settings.validate_settings(run["settings"])
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise settings.SettingsError("Fallback clock requires an aware datetime")
        start = datetime.fromisoformat(run["started_at"])
        if now < start:
            raise settings.SettingsError("Fallback run has not started")
        for policy in (current, frozen):
            minutes = policy["per_run_minutes"]
            if minutes is not None and now - start >= timedelta(minutes=minutes):
                raise settings.SettingsError("Fallback run deadline reached")
        return effective_policy(current, frozen, arguments.get("initial_url"))

    return run_session(root, run_id, session_id, policy={}, guard=guard,
                       refresh_policy=refresh, **arguments)


class _Blocked(Exception):
    def __init__(self, outcome, reason):
        self.outcome, self.reason = outcome, reason


def _directory(root, run_id, session_id):
    if any(not isinstance(x, str) or not _ID.fullmatch(x) for x in (run_id, session_id)):
        raise ValueError("run_id and session_id must be safe identifiers")
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Workspace does not exist")
    directory = root / "applications" / "runs" / run_id / "computer-use"
    # Even an in-workspace symlink could redirect private artifacts to public code.
    if directory.resolve() != directory:
        raise ValueError("Private run directory must not contain symlinks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root, directory


def _write(path, record):
    fd, temporary = tempfile.mkstemp(prefix=".computer-use-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _screenshot(root, run_dir, reference):
    if not isinstance(reference, str) or not reference:
        return None
    path = Path(reference)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if (not path.is_relative_to(run_dir) or not path.is_file()
            or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            or path.stat().st_size == 0):
        return None
    return path.relative_to(root).as_posix()


def _observation(adapter, root, run_dir, tab_id, origin, policy):
    observation = adapter.observe()
    if not isinstance(observation, Observation):
        raise TypeError("Adapter must return Observation")
    result = asdict(observation)
    result["screenshot"] = _screenshot(root, run_dir, observation.screenshot)
    try:
        current_origin, host = _origin(observation.url)
        scoped = (type(observation.tab_id) is str and observation.tab_id == tab_id
                  and current_origin == origin and observation.easy_apply is False
                  and _permitted(host, policy))
    except (ValueError, TypeError, UnicodeError):
        scoped = False
    if not scoped:
        return result, Outcome.OFF_SCOPE
    if result["screenshot"] is None:
        return result, Outcome.SCREENSHOT_MISSING
    return result, None


def _action_allowed(action, failure_kind):
    if (not isinstance(action, Action) or not isinstance(action.kind, str)
            or not isinstance(action.target, str) or not action.target.strip()):
        return False
    if action.kind == "upload_chosen_file":
        return failure_kind == "native_file_picker" and action.value is None
    return (failure_kind in {"canvas", "custom_widget"} and action.kind in _WIDGET_ACTIONS
            and isinstance(action.value, str))


def _resume_path(root, selected):
    if not isinstance(selected, (str, Path)):
        raise ValueError("A parent-resolved resume is required")
    path = Path(selected)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("A parent-resolved workspace file is required")
    return path


def _valid_record(record, binding):
    if (not isinstance(record, dict) or "outcome" not in record
            or type(record.get("schema_version")) is not int
            or any(record.get(k) != v for k, v in binding.items())):
        return False
    count, actions = record.get("step_count"), record.get("actions")
    budget = record.get("step_budget")
    if (type(budget) is not int or budget < 0 or type(count) is not int
            or not 0 <= count <= budget or not isinstance(actions, list) or len(actions) != count):
        return False
    for number, action in enumerate(actions, 1):
        if not isinstance(action, dict) or action.get("step") != number:
            return False
    return ((record.get("outcome") is None or (isinstance(record.get("outcome"), str)
            and record["outcome"] in {x.value for x in Outcome}))
            and isinstance(record.get("reason"), str))


def run_session(root, run_id, session_id, *, policy, adapter, initial_tab_id,
                initial_url, failure_kind, dom_failed, actions, resolved_resume=None,
                guard=None, refresh_policy=None, resume_resolver=None):
    """Execute a finite plan once; return a typed result, with private durable audit.

    `policy` is the parent's effective (current AND frozen-run) policy. `guard`
    is mandatory for interaction and must return exactly True at every adapter
    boundary. Prefer run_workspace_session to refresh settings/deadlines. `actions`
    is a list/tuple of Action objects. `resolved_resume` is the exact current file
    chosen by the parent's resume resolver, never a path from a widget or action.
    Alternatively resume_resolver() resolves the current release immediately
    before each upload; a resolver failure blocks the operation without fallback.
    A terminal session is never replayed. An interrupted action is never retried.
    Invalid storage/identifiers raise ValueError/OSError before any adapter call.
    """
    root, directory = _directory(root, run_id, session_id)
    path = directory / f"{session_id}.json"
    record_ref = path.relative_to(root).as_posix()
    lock_path = directory / f"{session_id}.lock"
    writer_path = root / "applications/computer-use.lock"
    writer_owned = False
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return Result(Outcome.FAILED, "session_busy", None, record_ref)
    try:
        os.close(lock_fd)
        # Copy the caller's snapshot so an adapter cannot mutate it mid-session.
        policy = deepcopy(policy)
        try:
            origin, host = _origin(initial_url)
        except (ValueError, TypeError, UnicodeError):
            origin, host = None, None
        binding = {"schema_version": 1, "run_id": run_id, "session_id": session_id,
                   "initial_tab_id": initial_tab_id, "initial_origin": origin,
                   "failure_kind": failure_kind}
        if path.is_symlink():
            return Result(Outcome.FAILED, "invalid_session_record", None, record_ref)
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                return Result(Outcome.FAILED, "invalid_session_record", None, record_ref)
            if not _valid_record(record, binding):
                return Result(Outcome.FAILED, "invalid_session_record", None, record_ref)
            if record["outcome"] is not None:
                return Result(Outcome(record["outcome"]), record["reason"], record["step_count"], record_ref)
            # A previous process stopped before a terminal checkpoint. Do not
            # assume the last operation failed, or silently replay its action.
            record["outcome"], record["reason"] = "failed", "interrupted_session"
            record["updated_at"] = _now()
            _write(path, record)
            return Result(Outcome.FAILED, record["reason"], record["step_count"], record_ref)

        record = {**binding, "step_budget": 6, "step_count": 0, "actions": [],
                  "outcome": None, "reason": "", "started_at": _now(), "updated_at": _now()}

        def finish(outcome, reason):
            record.update(outcome=outcome.value, reason=reason, updated_at=_now())
            _write(path, record)
            return Result(outcome, reason, record["step_count"], record_ref)

        if refresh_policy is not None:
            try:
                policy = deepcopy(refresh_policy())
            except Exception:
                return finish(Outcome.FAILED, "policy_refresh_refused")
        if not isinstance(policy, dict):
            return finish(Outcome.FAILED, "invalid_policy")
        computer = policy.get("computer_use", {"enabled": False, "step_budget": 6})
        if not isinstance(computer, dict):
            return finish(Outcome.FAILED, "invalid_policy")
        enabled = computer.get("enabled", False)
        budget = computer.get("step_budget", 6)
        if (type(enabled) is not bool or type(budget) is not int or budget < 0
                or set(computer) - {"enabled", "step_budget"}):
            return finish(Outcome.FAILED, "invalid_policy")
        record["step_budget"] = budget
        if not enabled:
            return finish(Outcome.DISABLED, "computer_use_disabled")
        if policy.get("mode", "apply") != "apply":
            return finish(Outcome.DISABLED, "mode_forbids_interaction")
        try:
            scoped = (host is not None and _permitted(host, policy)
                      and policy.get("allowed_origin", origin) == origin
                      and type(initial_tab_id) is str and bool(initial_tab_id.strip())
                      and dom_failed is True and isinstance(failure_kind, str) and failure_kind in _FAILURES)
        except (ValueError, TypeError, UnicodeError):
            scoped = False
        if not scoped:
            return finish(Outcome.OFF_SCOPE, "fallback_not_permitted")
        if not isinstance(actions, (list, tuple)) or not actions:
            return finish(Outcome.FAILED, "empty_or_invalid_plan")
        if any(not _action_allowed(action, failure_kind) for action in actions):
            return finish(Outcome.OFF_SCOPE, "action_not_permitted")
        actions = tuple(actions)  # Callbacks cannot append unvalidated operations.
        if adapter is None:
            return finish(Outcome.FAILED, "adapter_unavailable")
        if not callable(guard):
            return finish(Outcome.FAILED, "guard_required")
        resume_path = None
        if any(action.kind == "upload_chosen_file" for action in actions):
            if resume_resolver is not None and not callable(resume_resolver):
                return finish(Outcome.OFF_SCOPE, "resolved_resume_required")
            try:
                if resume_resolver is None:
                    resume_path = _resume_path(root, resolved_resume)
            except (ValueError, OSError):
                return finish(Outcome.OFF_SCOPE, "resolved_resume_required")

        # Shared across runs/sessions in this workspace, not merely this ID.
        try:
            writer_fd = os.open(writer_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return finish(Outcome.FAILED, "browser_busy")
        writer_owned = True
        os.close(writer_fd)

        def boundary(phase):
            nonlocal policy, budget
            if os.path.lexists(root / "applications/browser-claim.json"):
                raise _Blocked(Outcome.FAILED, "pending_submission_claim")
            try:
                if guard(phase) is not True:
                    raise _Blocked(Outcome.FAILED, "guard_refused")
            except _Blocked:
                raise
            except Exception:
                raise _Blocked(Outcome.FAILED, "guard_refused") from None
            if refresh_policy is not None:
                try:
                    policy = deepcopy(refresh_policy())
                    computer = policy["computer_use"]
                    new_budget = computer["step_budget"]
                    if (type(computer["enabled"]) is not bool or type(new_budget) is not int
                            or new_budget < 0):
                        raise ValueError
                    budget = min(budget, new_budget)
                    record["effective_step_budget"] = budget
                    if not computer["enabled"] or policy["mode"] != "apply":
                        raise _Blocked(Outcome.DISABLED, "policy_tightened")
                    if (not _permitted(host, policy)
                            or policy.get("allowed_origin", origin) != origin):
                        raise _Blocked(Outcome.OFF_SCOPE, "policy_tightened")
                except _Blocked:
                    raise
                except Exception:
                    raise _Blocked(Outcome.FAILED, "policy_refresh_refused") from None
            if record["step_count"] > budget:
                raise _Blocked(Outcome.BUDGET_EXHAUSTED, "step_budget_exhausted")
            # guard/policy refresh must not have created a submit claim.
            if os.path.lexists(root / "applications/browser-claim.json"):
                raise _Blocked(Outcome.FAILED, "pending_submission_claim")

        _write(path, record)
        for action in actions:
            if record["step_count"] >= budget:
                return finish(Outcome.BUDGET_EXHAUSTED, "step_budget_exhausted")
            try:
                boundary("observe")
                before, blocked = _observation(adapter, root, directory.parent,
                                               initial_tab_id, origin, policy)
            except _Blocked as blocked_guard:
                return finish(blocked_guard.outcome, blocked_guard.reason)
            except Exception:
                return finish(Outcome.FAILED, "adapter_observation_failed")
            if blocked:
                record["last_observation"] = before
                return finish(blocked, "before_action_" + blocked.value)
            if record["step_count"] >= budget:
                return finish(Outcome.BUDGET_EXHAUSTED, "step_budget_exhausted")
            entry = {"step": record["step_count"] + 1, "action": asdict(action),
                     "before": before, "after": None, "status": "pending", "started_at": _now()}
            if resume_path is not None and resume_resolver is None:
                entry["resume_ref"] = resume_path.relative_to(root).as_posix()
            record["actions"].append(entry)
            record["step_count"] += 1
            record["updated_at"] = _now()
            _write(path, record)  # Reserve budget and record intent before interaction.
            try:
                boundary("action")
                if action.kind == "upload_chosen_file" and resume_resolver is not None:
                    try:
                        resume_path = _resume_path(root, resume_resolver())
                    except Exception:
                        raise _Blocked(Outcome.FAILED, "resume_resolution_failed") from None
                    entry["resume_ref"] = resume_path.relative_to(root).as_posix()
            except _Blocked as blocked_guard:
                entry["status"] = "blocked"
                return finish(blocked_guard.outcome, blocked_guard.reason)
            if action.kind == "upload_chosen_file" and resume_resolver is not None:
                _write(path, record)  # A storage failure must prevent all adapter calls.
            action_failed = False
            try:
                if action.kind == "upload_chosen_file" and resume_resolver is not None:
                    # Resolution can cross a deadline or observe a new stop.
                    boundary("action")
                adapter.perform(action, tab_id=initial_tab_id, origin=origin, resume_path=resume_path)
            except _Blocked as blocked_guard:
                entry["status"] = "blocked"
                return finish(blocked_guard.outcome, blocked_guard.reason)
            except Exception:
                action_failed = True
            try:
                boundary("observe")
                after, blocked = _observation(adapter, root, directory.parent,
                                              initial_tab_id, origin, policy)
                entry["after"] = after
            except _Blocked as blocked_guard:
                entry["status"] = "blocked"
                return finish(blocked_guard.outcome, blocked_guard.reason)
            except Exception:
                entry["status"] = "failed"
                return finish(Outcome.FAILED, "adapter_after_observation_failed")
            if blocked:
                entry["status"] = blocked.value
                return finish(blocked, "after_action_" + blocked.value)
            if after["screenshot"] == before["screenshot"]:
                entry["status"] = "screenshot_missing"
                return finish(Outcome.SCREENSHOT_MISSING, "distinct_after_screenshot_required")
            if action_failed:
                entry["status"] = "failed"
                return finish(Outcome.FAILED, "adapter_action_failed")
            entry["status"], entry["finished_at"] = "completed", _now()
            record["updated_at"] = _now()
            _write(path, record)
        return finish(Outcome.COMPLETED, "plan_completed")
    finally:
        if writer_owned:
            writer_path.unlink()
        lock_path.unlink()
