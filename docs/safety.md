# Account-safety integration

`scripts/safety.py` uses only the Python standard library. All paths are relative
to the explicitly selected private workspace; no module import starts a browser,
reads a profile, or sends a notification. Tests create temporary synthetic data.
`workspace.py` and `runner_workspace.py` connect the helpers to workspace
operations. `schedule_run.py` checks safety before creating a scheduled run and
before each runner step; `scripts/seen.sh` provides the shell-only lookup refusal
described below. These hooks do not themselves inspect a live browser page.

## Python API

```python
from safety import (
    detect, guard, stop_run, SafetyError, SafetyBlocked, EvidenceRequired,
    notify_stop, local_notification, resume_stop, reconcile,
)
```

Pass a workspace `Path`, a non-empty safe run ID and an aware `datetime`.
Persisted timestamps retain the caller’s configured timezone and explicit offset.
Identifiers use 1–128 letters, digits, underscores or hyphens, starting with a
letter or digit. Artifacts must not traverse workspace symlinks.

`guard(root, run_id, now, command)` returns `None` when work may proceed. It raises
`SafetyBlocked` when STOP, the shell's pending marker or a durable unresolved stop
exists. `exc.stop_reason` is `manual_stop` or `account_safety`; `exc.record` is
private state. The exception message contains no screenshot path or page text.
Use a simple public command name (`seen`, `capture`, `preflight`), without form
answers or CLI arguments. The first refusal writes the run's stop record once.
There is no magic command-name bypass in `guard`.

`detect(page_text, url="")` returns `Detection(kind, signal,
stop_reason="account_safety")`, or `None`. Kinds are `captcha`, `challenge`,
`unusual_activity`, `sign_in`, `rate_limit`, `restriction`. `signal` is the static
label `url_path` or `page_text`; the result retains no raw page text or URL. URL
checks examine the path, not token-bearing query parameters. Text checks accept
rendered text or simple HTML, normalize whitespace and ignore case. A normal
navigation-bar Sign in link alone does not establish a login wall. These
conservative detectors consume the **current page**, not stored listing prose;
they are not a general classifier for every provider's warning wording.

`stop_run(root, run_id, reason, now, evidence=None, notify=None)` persists an
unresolved incident and the run stop record, returning the private incident.
It does **not** raise on a successful stop: the caller must exit the entire loop.
Reasons are `manual_stop` and `account_safety`. Account-safety evidence is:

```python
evidence = {
    "screenshot": "applications/runs/example/evidence/challenge.png",
    "kind": detection.kind,
}
```

The browser adapter must capture the real current page and supply that existing
file. Absolute references inside this workspace's `applications/` are accepted
and stored as relative paths. Supported reference suffixes are PNG, JPG, JPEG and
WebP. The helper checks location/existence, but never reads image contents and
cannot attest how an adapter obtained the image. It creates no screenshot and
copies no image bytes. Other evidence keys, page text and URLs are discarded.

Missing or invalid evidence raises `EvidenceRequired` (a `SafetyError`) **after**
the account-safety stop is durable and its opted-in notification is attempted.
Neither error authorizes continuation. If capture fails, record the typed blocker,
end the loop, and obtain a real screenshot through the adapter when available;
call `stop_run` again to fill the missing reference without replacing the stop.
`SafetyError` and filesystem errors must both fail closed at every caller.

## Integrated workspace hooks

`workspace.py` calls `safety.guard` through `work_guard` on guarded workspace
operations, and `runner_workspace.py` calls that gate from the runner's
mandatory per-action guard. The gate also recovers interrupted Submit state and
checks the browser-session retry guard where applicable. `scripts/seen.sh`
checks STOP and both safety sentinels using shell built-ins before reading its
lookup index; when STOP is present, it persists `SAFETY_STOP_PENDING` and exits
2. A later Python guard materializes or restores the durable incident state.

The `safety-check` CLI accepts a supplied JSON file containing page text and URL,
runs `detect`, and, on a detection, calls `stop_run` with the optional screenshot
reference. It does not capture a screenshot or connect to a browser. The helper
cannot establish that supplied page text, URL, or screenshot came from the live
page. A real browser adapter must provide genuine current-page observations and
screenshot evidence before this path can be treated as live validation.

On guarded refusals, `workspace.py` attempts the local notification callback;
the generic `guard` persists safety state without delivering notifications. The explicit
`workspace.py resume-stop --run-id ... --operator ... --note ...` command calls
`resume_stop` with the operator's supplied review evidence. STOP must first be
removed; removing it alone, choosing another run ID, or using `resume-stop` as a
guard command does not resolve an incident. Resolution remains in the incident
ledger, and later work should use a new run ID. Historical confirmation logging,
exports, state inspection and explicit reconciliation remain available.

## Python-free seen hook

The integrated `scripts/seen.sh` uses this shell-only refusal before any lookup.
It uses shell built-ins and preserves a pending record even if STOP is removed
before Python next runs. It neither invokes Python nor sends notifications:

```sh
if [ -e "$root/applications/STOP" ] || [ -L "$root/applications/STOP" ] ||
   [ -e "$root/applications/SAFETY_STOP" ] || [ -L "$root/applications/SAFETY_STOP" ] ||
   [ -e "$root/applications/SAFETY_STOP_PENDING" ] || [ -L "$root/applications/SAFETY_STOP_PENDING" ]; then
    # STOP lives in applications/, so its parent already exists.
    # Persist observation before refusing; never erase this marker on startup.
    if [ -e "$root/applications/STOP" ] || [ -L "$root/applications/STOP" ]; then
        : > "$root/applications/SAFETY_STOP_PENDING" || exit 2
    fi
    printf '%s\n' 'BLOCKED: safety_stop; run the Python guard to record/reconcile' >&2
    exit 2
fi
```

On the next guarded Python workspace or runner operation, the guard uses the
same run ID retained by the runner (`APPLYD_RUN_ID` or an explicit CLI argument).
This materializes the incident and run record; a pending marker itself blocks
work even after STOP removal. A shell-only caller cannot deliver a notification
or write the full timestamped run JSON; the guarded Python path records the
refusal and attempts its configured local notification. Do not claim shell
refusal alone delivered a notification. At startup the Python guard also checks
the incident ledger and restores a missing sentinel after an interrupted write.
The shell fast path trusts the durable sentinel; do not delete it manually.

## Private durable artifacts

| Artifact | Contract |
| --- | --- |
| `applications/STOP` | Manual kill switch. Presence, including a dangling symlink, blocks work. Never automatically removed. |
| `applications/SAFETY_STOP_PENDING` | Empty shell observation marker; removal requires explicit reconciliation. |
| `applications/SAFETY_STOP` | Shell-readable existence sentinel; JSON contains only `stop_id`. |
| `applications/safety/stops/<stop-id>.json` | Authoritative incident, first reason/time, private screenshot reference, notification delivery marker and explicit resolution. |
| `applications/runs/<run-id>/safety.json` | Immutable first refusal for that run: stopped status/reason/time, incident ID and optional public command name. No image reference or contents. |
| `applications/safety/.lock` | Cross-process advisory lock for guard/stop/notify/resume. |

Existing `applications/runs/<run-id>/run.json` settings snapshots are untouched.
Repeated stops, including another run blocked by the same incident, retain the
first incident and notification identity. A later account-safety detection during
an existing manual stop adds `account_safety: true` and its required evidence;
it cannot overwrite the first recorded reason. All work remains blocked.
Atomic JSON replacement, file/directory fsync and an advisory lock protect normal
process races and interrupted writes. Corrupt state blocks work. This is a local
safety gate, not a security boundary against someone rewriting the workspace.

All these artifacts stay private under the existing publication deny rule.
Public logs/notifications may contain the stable stop reason and opaque IDs,
never the private incident dict, screenshot path/contents, page text or URL query
tokens. The helper does not itself publish these artifacts.

## Notification delivery

No notification runs by default. `notify` is an explicit callback receiving only
`stop_id`, originating `run_id`, `stop_reason`. A callback returning normally means
delivery succeeded; it must raise on failure. `local_notification` calls
`subprocess.run(["osascript", "-e", fixed_script], ..., timeout=10)` without a
shell. Only an explicitly opted-in local run should provide it. The library
cannot determine whether a caller represents operator intent; that is the
runner/CLI's responsibility. The included CLI requires `--notify-local`.

Delivery status is durable: `pending` → `attempting` → `delivered` or `failed`.
The attempt reservation is committed before invoking the callback. Repeated
stops, another process, or a restart cannot deliver that stop again. Failure
records only the exception type; provider error text is not logged. A crash
during delivery leaves `attempting`, which must be reviewed manually and is never
replayed automatically. An external notification and a filesystem transaction
cannot guarantee exactly-once delivery across a crash; this implementation
guarantees at most one attempt and does not claim success without a successful
callback. A pending notification can be explicitly delivered with `notify_stop`.

Example local commands (the `stop` and blocked `guard` commands exit 2):

```sh
python3 scripts/safety.py --workspace /private/workspace --run-id example guard preflight
python3 scripts/safety.py --workspace /private/workspace --run-id example --notify-local stop account_safety --kind challenge --screenshot applications/runs/example/evidence/challenge.png
python3 scripts/safety.py --workspace /private/workspace --run-id example resume-stop --operator local-user --note 'Reviewed the challenge in the browser and resolved it'
```

Synthetic verification: `python3 -m unittest discover -s tests -p test_safety.py -v`.
No live pages, employer submissions, credentials, real screenshots or real
notifications are used by the tests.
