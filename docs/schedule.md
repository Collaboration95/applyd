# Local scheduled runs

`scripts/schedule_run.py` performs one local tick and exits. It does not install
a schedule, start a background process, load browser credentials, or send mail.
Python 3.11+ and POSIX `fcntl` are required (macOS/Linux; Windows requires WSL).

Keep the schedule and all generated artifacts in a private workspace outside
the code checkout. The workspace selector is required; this CLI does not fall
back to the checkout or an environment variable.

## Configuration

Save this strict JSON as `schedule.json` in a synthetic workspace first:

```json
{
  "schema_version": 1,
  "enabled": true,
  "id": "weekday-easy",
  "weekdays": [0, 1, 2, 3, 4],
  "start": "09:00",
  "end": "09:15",
  "timezone": null,
  "target": 10,
  "max_steps": 60
}
```

All fields are required. Unknown or duplicate keys, nonfinite numbers, and
incorrect types are rejected. IDs use 1–32 lowercase letters, digits or hyphens,
starting with a letter or digit. Weekdays use Monday=0 through Sunday=6 without
duplicates. Times must use `HH:MM`, with start strictly before end on the same
day. The start is inclusive; the end is exclusive. `timezone: null` inherits
`settings.json`; an IANA name such as `Asia/Singapore` overrides only the due
window. Run timestamps/deadlines still use the settings timezone.

`target` is a positive confirmation target; the effective target is the minimum
of this value, `per_run_cap`, and `daily_cap`. The runner also counts confirmations
already made today. `max_steps` is 1–100 adapter actions, including discovery,
preparation, review and submission. Ten confirmations can require more than ten
steps. An adapter action must itself have finite provider/browser timeouts.

Unattended scheduling requires finite, nonnegative integer `daily_cap`,
`per_run_cap`, and `per_run_minutes` settings. Null/unlimited settings cause a
skip before `run.json` or an adapter call. Zero limits permit no new actions.
For the example above, configure these settings in the workspace:

```json
{
  "mode": "apply",
  "timezone": "Asia/Singapore",
  "easy_apply_only": true,
  "daily_cap": 10,
  "per_run_cap": 10,
  "per_run_minutes": 20,
  "work_budget": {"max_actions": 60, "max_retries": 3}
}
```

These are settings fields to merge with your existing policy, not permission to
replace profile, resume or eligibility settings. Easy Apply selection belongs
in settings. No schedule can relax the immutable run snapshot or current runner
gates. Missing measured provider usage remains unknown; configured provider
budgets use the existing runner's handoff behavior.

The window controls **starting**, not finishing. A tick at 09:14 can continue
until its configured run deadline. No missed-window catch-up occurs. A DST fold
shares one daily slot; a skipped civil window produces no catch-up run.

## CLI and Python API

Run a plan-only tick against a temporary synthetic workspace:

```sh
python3 /path/to/applyd/scripts/schedule_run.py \
  --workspace /path/to/synthetic-workspace \
  --config /path/to/synthetic-workspace/schedule.json \
  --dry-run --now 2026-10-20T09:00:00+08:00
```

`--now` requires an offset and is allowed only with `--dry-run`. Dry runs call
the real runner's planner, save local plans/receipts and exports, and plan tab
cleanup. They do not call application or cleanup adapters. The frozen settings
are preserved; if their mode is `dry_run` or `save_only`, the runner returns
`mode_requires_manual_handoff`. This is a policy refusal, not execution success.

Without `--dry-run`, the standalone CLI has no application adapter and returns
`manual_handoff` / `adapter_missing` when work would require one. It does not
claim any application succeeded. Live integration must supply explicitly
authorized adapters through the Python API:

```python
from pathlib import Path
from schedule_run import tick  # scripts/ must be on the import path

result = tick(
    Path("/path/to/private-workspace"),
    Path("/path/to/private-workspace/schedule.json"),
    adapter=authorized_application_adapter,
    cleanup_adapter=authorized_cleanup_adapter,
    execute_cleanup=True,
    notify=local_summary_callback,
)
```

The application adapter follows `runner_workspace.build_runner`'s contract,
including `observe_url(action)` for submit identity and saved confirmation
evidence. It receives the deterministic run ID, also used as the slot's batch
ID; discoveries and prepared attempts must retain that context for batch credit.
The cleanup adapter supplies `enumerate_tabs()` and `close_tab(window_id, tab_id)`.
Execution requires both `execute_cleanup=True` and an explicit adapter; there is
no implicit native browser cleanup fallback. Otherwise cleanup is planned.
Only registered and freshly reverified tabs are candidates; uncertain tabs stay
open. Do not wrap `tick` in `workspace.writer`: the runner locks each action and
the scheduler acquires that lock separately for final bookkeeping.

`notify(payload)` receives exactly `summary_uri` (a private `file://` JSON summary
link) and `waiting_questions` (the count of distinct unresolved canonical inbox
questions, freshly resolved against saved answers). If counting fails, the count
is null with `question_count_failed` in the summary. The callback runs after the
workspace writer is released. Default notification delivery is disabled.

The optional CLI `--notify-local` explicitly enables a macOS notification. Its
private URI and count travel through stdin to `osascript -`, with no shell or
private data in process arguments. Unsupported platforms or delivery failures
record `notification_failed`; no mail or external message is sent. Existing live
workspace/browser adapters can have their own stop-notification behavior; the
scheduler's pre-step safety guard avoids that legacy path for stops it observes.

CLI stdout contains only status, a fixed reason code, and an available question
count. It excludes run IDs, workspace paths, summary URIs and provider exception
text. Exit 0 means idle, a consumed slot, or successful quota completion/planning;
exit 2 means a skip, handoff or finalization failure. API results include private
artifact links for local callers. Do not copy those results into shared logs.

## Receipts, recovery and finalization

Every schedule ID has at most one slot per local date, named
`schedule-<id>-YYYYMMDD`. Changing its time, target or limits within that date does
not create a second attempt. Both dry and live ticks consume the same slot;
use a separate synthetic workspace for rehearsal. A safety/settings skip also
consumes its slot. There is no automatic same-day retry after an operator fixes
a blocker.

A nonblocking workspace-wide schedule writer prevents overlap across schedule
IDs and remains held through cleanup and notification. Slot receipts under
`applications/schedule/slots/` are atomically persisted before `run_start`.
The schedule, settings file and immutable `run.json` are never rewritten by the
wrapper. The runner owns its own per-action workspace lock; these locks are not
nested around runner execution. Other manual browser writers still require the
existing workspace/session coordination.

Before a new attempt, `safety.guard` rejects STOP and unresolved account-safety
incidents, including an unresolved ledger whose sentinel was removed. This can
persist a skip receipt and safety record, but creates no `run.json` and calls no
adapter. Existing pending runner actions or a browser submit claim also block
new scheduled runs before start.

Once claimed, every normally exiting attempt tries cleanup, export recovery,
summary creation, question counting and optional notification independently.
Submit-claim recovery runs before cleanup so an interrupted claim becomes
uncertain and its tab remains open. If that recovery fails, cleanup is plan-only.
The private `applications/runs/<run-id>/schedule-summary.json` records fixed
outcome/finalizer reasons and links to cleanup's `summary.md` when available.
Cleanup, notification and export failures do not authorize replay. Delivery
intent is persisted before calling a notification callback; an interrupted
delivery remains `attempting` and is never automatically sent twice.

A hard kill or power loss cannot run finalizers immediately. The next tick
first finalizes an unfinished receipt with **plan-only** cleanup and a
`reconciliation_required` handoff, even outside the window or with the schedule
disabled. It does not construct a runner or invoke application/cleanup adapters.
Existing pending actions remain pending. Use the existing evidence-based runner
and submission reconciliation workflow before another scheduled attempt; never
delete receipts, pending state, or intent records to force a retry. A prior slot
is never replayed even after reconciliation. Persistent storage failure can
prevent durable summaries/notifications; preserve the receipt and repair storage
before invoking another tick.

## Manual trigger setup

The following setup is performed by the operator. This implementation does not
run any installation commands or enable a scheduler. Use an absolute Python
path and absolute workspace/config paths, and verify the synthetic tick first.
The plain CLI remains useful for dry runs and typed adapter handoffs. To perform
live work, point your trigger at your authorized integration entrypoint that
calls `tick` with the adapters above.

For cron, open `crontab -e` yourself and add a minute tick, replacing the example
paths. Polling each minute leaves weekday/timezone enforcement to the config:

```cron
* * * * * /absolute/python3 /path/to/applyd/scripts/schedule_run.py --workspace /path/to/synthetic-workspace --config /path/to/synthetic-workspace/schedule.json --dry-run
```

Keep any redirected logs private. Remove the entry with `crontab -e` to stop
triggers; disable the schedule to stop new slots while retaining recovery.

For launchd on macOS, manually save this local example as
`~/Library/LaunchAgents/org.applyd.schedule.plist`, replacing paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.applyd.schedule</string>
  <key>ProgramArguments</key><array>
    <string>/absolute/python3</string>
    <string>/path/to/applyd/scripts/schedule_run.py</string>
    <string>--workspace</string><string>/path/to/synthetic-workspace</string>
    <string>--config</string><string>/path/to/synthetic-workspace/schedule.json</string>
    <string>--dry-run</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
</dict></plist>
```

Validate with `plutil -lint` first. The operator can then use `launchctl bootstrap`
with their GUI domain and the local plist path to enable it, and `launchctl bootout`
with that domain/path to remove it. Do not put private summary contents or
credentials in the plist. Sleeping/logged-out machines can miss a window; no
catch-up or successful notification delivery is guaranteed by these triggers.

## Synthetic verification

```sh
python3 -m unittest discover -s tests -p test_schedule_run.py -v
```

Tests use temporary workspaces, fixture resume bytes, real settings/runner/budget/
preflight gates, stub application/cleanup adapters and stub notifications. They
cover confirmations bounded by count, time and steps; current/frozen limits;
duplicate/concurrent ticks; due windows/DST; STOP/account safety; missing/failing
adapters; hard-exit recovery; finalization failures; canonical question counts;
privacy-safe CLI output; and a subprocess dry-run smoke. No authenticated browser,
employer submission, real notification or installed schedule is exercised. Live
adapter correctness, provider timeouts and native desktop behavior require
separate authorized verification.
