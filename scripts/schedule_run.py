#!/usr/bin/env python3
"""One bounded local schedule tick; no daemon, installation, or browser driver."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cleanup
from dashboard_payload import enrich_payload
from runner import Kind
from runner_workspace import build_runner
import safety
import settings
import workspace as w


class ScheduleError(ValueError):
    """Only fixed public reason codes cross the CLI boundary."""


FIELDS = {"schema_version", "enabled", "id", "weekdays", "start", "end",
          "timezone", "target", "max_steps"}
REASONS = frozenset({
    "adapter_missing", "quota_reached", "deadline_reached", "run_not_started",
    "action_budget_reached", "provider_budget_reached", "provider_usage_unavailable",
    "budget_state_missing", "mode_requires_manual_handoff", "gate_blocked",
    "metrics_unavailable", "submission_uncertain", "unverified_action", "not_executed",
    "unsupported", "timeout", "login", "page_changed", "account_safety",
    "retry_exhausted", "retry_waiting", "retry_reconciliation_required",
})


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ScheduleError("invalid_json")
        value[key] = item
    return value


def _invalid_constant(_):
    raise ScheduleError("invalid_json")


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"),
                          object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (OSError, ValueError, UnicodeError):
        raise ScheduleError("invalid_json") from None


def validate_schedule(raw):
    """Exact schema, Monday=0; a same-day half-open civil-time window."""
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ScheduleError("invalid_schedule")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ScheduleError("invalid_schedule")
    if type(raw["enabled"]) is not bool:
        raise ScheduleError("invalid_schedule")
    if not isinstance(raw["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", raw["id"]):
        raise ScheduleError("invalid_schedule")
    days = raw["weekdays"]
    if (not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days)
            or len(set(days)) != len(days)):
        raise ScheduleError("invalid_schedule")
    for key in ("start", "end"):
        if not isinstance(raw[key], str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", raw[key]):
            raise ScheduleError("invalid_schedule")
    if raw["start"] >= raw["end"]:
        raise ScheduleError("invalid_schedule")
    if raw["timezone"] is not None:
        try:
            if not isinstance(raw["timezone"], str):
                raise ValueError
            ZoneInfo(raw["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            raise ScheduleError("invalid_schedule") from None
    if type(raw["target"]) is not int or raw["target"] < 1:
        raise ScheduleError("invalid_schedule")
    if type(raw["max_steps"]) is not int or not 1 <= raw["max_steps"] <= 100:
        raise ScheduleError("invalid_schedule")
    return {**raw, "weekdays": list(days)}


def load_schedule(path):
    return validate_schedule(_read(path))


def _time(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleError("invalid_time")
    return now


def due_slot(schedule, policy, now):
    """No catch-up. DST folds and schedule edits share one ID per civil date."""
    schedule = validate_schedule(schedule)
    local = _time(now).astimezone(ZoneInfo(schedule["timezone"] or policy["timezone"]))
    if (not schedule["enabled"] or local.weekday() not in schedule["weekdays"]
            or not schedule["start"] <= local.strftime("%H:%M") < schedule["end"]):
        return None
    return f"schedule-{schedule['id']}-{local:%Y%m%d}"


def _path(root, relative):
    path = root / relative
    if path.resolve() != path:
        raise ScheduleError("unsafe_artifact_path")
    return path


def _write(root, relative, value):
    w.atomic_json(_path(root, relative), value)


@contextmanager
def _schedule_writer(root):
    """Separate nonblocking lock, held through finalization; never unlink it."""
    path = _path(root, "applications/schedule/writer.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScheduleError("schedule_busy") from None
        yield
    finally:
        os.close(fd)


def _receipt_ref(run_id):
    if not re.fullmatch(r"schedule-[a-z0-9-]{1,32}-[0-9]{8}", run_id):
        raise ScheduleError("invalid_receipt")
    return f"applications/schedule/slots/{run_id}.json"


def _receipts(root):
    result = []
    for path in sorted(_path(root, "applications/schedule/slots").glob("*.json")):
        doc = _read(_path(root, path.relative_to(root)))
        if (not isinstance(doc, dict) or doc.get("schema_version") != 1
                or doc.get("phase") not in {"claimed", "started", "finalizing", "finished", "skipped"}
                or path.name != Path(_receipt_ref(doc.get("run_id", ""))).name):
            raise ScheduleError("invalid_receipt")
        result.append(doc)
    return result


def _pending(root):
    """Any unfinished runner/submit claim blocks a new scheduled attempt."""
    if _path(root, "applications/browser-claim.json").exists():
        return True
    for path in sorted(_path(root, "applications/runs").glob("*/state.json")):
        state = _read(_path(root, path.relative_to(root)))
        if not isinstance(state, dict) or "pending" not in state:
            raise ScheduleError("invalid_runner_state")
        if state["pending"] is not None:
            return True
    return False


def waiting_questions(root, now):
    """Canonical inbox count, freshly resolved with the dashboard's pure resolver."""
    inbox = enrich_payload(root, w.records(root), {}, now)["inbox"]
    return len({item["question_id"] for item in inbox["questions"] if item["status"] == "unresolved"})


def local_notification(payload):
    """Explicit macOS opt-in. Private URI goes over stdin, never argv or stdout."""
    if sys.platform != "darwin":
        raise ScheduleError("local_notification_unavailable")
    def literal(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
    count = payload["waiting_questions"]
    message = f"Questions waiting: {count if count is not None else 'unavailable'}\n{payload['summary_uri']}"
    script = f'display notification {literal(message)} with title "applyd run ended"\n'
    subprocess.run(["osascript", "-"], input=script, text=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=10)


def _finalize(root, receipt, now, *, notify=None, cleanup_adapter=None, execute_cleanup=False):
    """Attempt every independent finalizer even when a preceding one fails."""
    run_id = receipt["run_id"]
    ref = f"applications/runs/{run_id}/schedule-summary.json"
    receipt["phase"] = "finalizing"
    _write(root, _receipt_ref(run_id), receipt)
    summary = {"schema_version": 1, "run_id": run_id, "ended_at": now.isoformat(),
               "reason": receipt["reason"], "dry_run": receipt["dry_run"],
               "submit_recovery": "pending", "cleanup": "pending", "export": "pending", "waiting_questions": None,
               "question_count": "pending", "run_summary": None,
               "notification": receipt.get("notification", {"status": "not_requested"})}
    # Even a broken cleanup helper leaves this private summary to link to.
    _write(root, ref, summary)
    try:
        with w.writer(root):
            recovery = w.recover_submit(root, now)
            if recovery["recovered"]:
                w.finish_mutation(root, recovery, deferred=True)
        summary["submit_recovery"] = "recovered" if recovery["recovered"] else "not_needed"
    except (Exception, KeyboardInterrupt, SystemExit):
        summary["submit_recovery"] = "submit_recovery_failed"
    try:
        with w.writer(root):
            execute = execute_cleanup and not receipt["dry_run"] and summary["submit_recovery"] != "submit_recovery_failed"
            if execute and cleanup_adapter is None:
                raise ScheduleError("cleanup_adapter_missing")
            result = cleanup.cleanup_run(root, run_id, now, adapter=cleanup_adapter if execute else None,
                                         execute=execute)
        summary["cleanup"] = ("cleanup_failed" if result["failures"] else "executed") if execute else "planned"
        summary["run_summary"] = f"applications/runs/{run_id}/summary.md"
    except (Exception, KeyboardInterrupt, SystemExit):
        summary["cleanup"] = "cleanup_failed"
    try:
        with w.writer(root):
            outcome = w.recover(root, w.export_csv)
        summary["export"] = "exported" if outcome["ok"] else "export_failed"
    except (Exception, KeyboardInterrupt, SystemExit):
        summary["export"] = "export_failed"
    try:
        with w.writer(root):
            summary["waiting_questions"] = waiting_questions(root, now)
        summary["question_count"] = "counted"
    except (Exception, KeyboardInterrupt, SystemExit):
        summary["question_count"] = "question_count_failed"
    # Commit delivery intent first. Neither failure nor a hard crash permits replay.
    if notify is not None and "notification" not in receipt:
        summary["notification"] = receipt["notification"] = {"status": "attempting"}
        _write(root, ref, summary)
        _write(root, _receipt_ref(run_id), receipt)
        try:
            notify({"summary_uri": _path(root, ref).as_uri(),
                    "waiting_questions": summary["waiting_questions"]})
        except (Exception, KeyboardInterrupt, SystemExit):
            receipt["notification"] = {"status": "failed", "reason": "notification_failed"}
        else:
            receipt["notification"] = {"status": "delivered"}
        summary["notification"] = receipt["notification"]
    _write(root, ref, summary)
    receipt.update(phase="finished", ended_at=now.isoformat(), summary_ref=ref,
                   notification=summary["notification"])
    _write(root, _receipt_ref(run_id), receipt)
    complete = receipt["reason"] in {"quota_reached", "dry_run_planned"}
    failed = any(summary[k] in {"cleanup_failed", "export_failed", "question_count_failed", "submit_recovery_failed"}
                 for k in ("cleanup", "export", "question_count", "submit_recovery")) or summary["notification"]["status"] == "failed"
    return {"status": "finished" if complete and not failed else "manual_handoff",
            "reason": receipt["reason"], "run_id": run_id,
            "summary_uri": _path(root, ref).as_uri(), "waiting_questions": summary["waiting_questions"]}


def tick(root, config, *, now=None, clock=None, dry_run=False, adapter=None,
         cleanup_adapter=None, execute_cleanup=False, notify=None):
    """Consume at most one due slot. Adapters are explicit authorized injections.

    Config is a path or a schema dict. No setting/config/run snapshot is edited.
    Dry runs persist plans/receipts but never call either application adapter.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ScheduleError("workspace_missing")
    schedule = validate_schedule(config) if isinstance(config, dict) else load_schedule(config)
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = _time(now if now is not None else clock())
    with _schedule_writer(root):
        receipts = _receipts(root)
        unfinished = next((r for r in receipts if r["phase"] in {"claimed", "started", "finalizing"}), None)
        if unfinished is not None:
            # Recovery only: never build/execute a runner, enumerate tabs, or resubmit.
            unfinished["reason"] = "reconciliation_required"
            result = _finalize(root, unfinished, now, notify=notify)
            return {**result, "status": "manual_handoff"}
        policy = settings.load_settings(root)
        run_id = due_slot(schedule, policy, now)
        if run_id is None:
            return {"status": "idle", "reason": "not_due"}
        existing = next((r for r in receipts if r["run_id"] == run_id), None)
        if existing is not None:
            return {"status": "skipped", "reason": "slot_consumed", "run_id": run_id}
        receipt = {"schema_version": 1, "run_id": run_id, "phase": "claimed",
                   "claimed_at": now.isoformat(), "dry_run": bool(dry_run), "schedule": schedule}
        reason = None
        try:
            safety.guard(root, run_id, now, "schedule")
            if _pending(root) or _path(root, f"applications/runs/{run_id}/run.json").exists():
                reason = "reconciliation_required"
            elif any(policy[k] is None for k in ("daily_cap", "per_run_cap", "per_run_minutes")):
                reason = "unbounded_settings"
        except safety.SafetyBlocked as exc:
            reason = "account_safety" if exc.stop_reason == "account_safety" else "manual_stop"
        except (Exception, KeyboardInterrupt, SystemExit):
            reason = "prestart_guard_failed"
        if reason:
            receipt.update(phase="skipped", reason=reason)
            _write(root, _receipt_ref(run_id), receipt)
            return {"status": "skipped", "reason": reason, "run_id": run_id}
        # Receipt is fsynced before run_start; a crash cannot reuse this slot.
        receipt["reason"] = "run_failed"
        _write(root, _receipt_ref(run_id), receipt)
        try:
            frozen = settings.run_start(root, run_id, now)
            # Recheck the actual frozen policy (settings may change during start).
            if any(frozen["settings"][k] is None for k in ("daily_cap", "per_run_cap", "per_run_minutes")):
                raise ScheduleError("unbounded_settings")
            target = min(schedule["target"], frozen["settings"]["per_run_cap"], frozen["settings"]["daily_cap"])
            receipt.update(phase="started", target=target)
            _write(root, _receipt_ref(run_id), receipt)
            brief = f"applications/runs/{run_id}/schedule-batch.json"
            _write(root, brief, {"schema_version": 1, "batch_id": run_id, "target": target})
            controller = build_runner(root, run_id, _path(root, brief), None if dry_run else adapter)
            # A direct safety guard before each step avoids invoking a browser or
            # the workspace's legacy stop-notification path for an observed stop.
            if dry_run:
                safety.guard(root, run_id, _time(clock()), "schedule")
                action = controller.plan_next(_time(clock()))
                receipt["reason"] = action.reason if action.reason in REASONS else "dry_run_planned"
            else:
                for _ in range(schedule["max_steps"]):
                    safety.guard(root, run_id, _time(clock()), "schedule")
                    actions = controller.execute(clock=clock, max_steps=1)
                    action = actions[-1]
                    if action.kind in {Kind.HANDOFF, Kind.COMPLETE}:
                        receipt["reason"] = action.reason if action.reason in REASONS else "runner_handoff"
                        break
                else:
                    receipt["reason"] = "step_limit_reached"
        except safety.SafetyBlocked as exc:
            receipt["reason"] = "account_safety" if exc.stop_reason == "account_safety" else "manual_stop"
        except (KeyboardInterrupt, SystemExit):
            receipt["reason"] = "run_interrupted"
        except Exception:
            receipt["reason"] = "run_failed"
        finally:
            try:
                ended = _time(clock())
            except (Exception, KeyboardInterrupt, SystemExit):
                ended = now
            result = _finalize(root, receipt, ended, notify=notify, cleanup_adapter=cleanup_adapter,
                               execute_cleanup=execute_cleanup)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--notify-local", action="store_true")
    parser.add_argument("--now", help="Offset-aware ISO fixture time; only with --dry-run")
    args = parser.parse_args(argv)
    try:
        if args.now and not args.dry_run:
            raise ScheduleError("fixture_time_requires_dry_run")
        now = _time(datetime.fromisoformat(args.now)) if args.now else None
        result = tick(args.workspace, args.config, now=now,
                      clock=(lambda: now) if now else None, dry_run=args.dry_run,
                      notify=local_notification if args.notify_local else None)
        # Private IDs, paths, answers and provider text never enter CLI logs.
        print(json.dumps({k: result[k] for k in ("status", "reason", "waiting_questions") if k in result}))
        return 0 if result["status"] in {"idle", "finished"} or result["reason"] == "slot_consumed" else 2
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        reason = str(exc) if isinstance(exc, ScheduleError) else "schedule_failed"
        print(json.dumps({"status": "manual_handoff", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
