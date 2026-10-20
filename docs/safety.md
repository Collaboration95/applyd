# Account-safety integration

`scripts/safety.py` uses only the Python standard library. All paths are relative
to the explicitly selected private workspace; no module import starts a browser,
reads a profile, or sends a notification. Tests create temporary synthetic data.
The parent owns extractor, `seen.sh`, workspace/skill wiring and the publication
allowlist. These three new files do not install those hooks by themselves.

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

## Exact parent hooks

1. Resolve the workspace and current run ID before any new application work.
   Call `guard(root, run_id, now, command)` before seen lookups, listing capture,
   preparation, preflight and any browser work. Require a run ID for these new
   safety hooks; do not derive a changing ID per retry or bypass legacy commands.
   A scheduled run must guard before settings run creation/browser startup.
2. On every extractor/current-page snapshot, and before any form click, call
   `detect(page_text, current_url)`. When it matches, capture the current page
   using the browser adapter, pass the resulting private screenshot reference to
   `stop_run(..., "account_safety", ..., evidence, notify=callback)`, and end the
   entire run. Do not capture the listing or attempt the next job. If capture is
   unavailable, call `stop_run` with missing evidence and handle its typed blocker.
   The JS extractor should return the typed detector signal to the Python parent;
   it must not invent a screenshot field or mark an application submitted.
3. Within the existing single-writer submit section, guard again immediately
   before Submit, alongside settings, resume, answer and duplicate checks. A stop
   discovered after preflight must prevent the click. Stop policy cannot undo a
   click already sent; reconcile any uncertain submission using real evidence.
4. Catch `SafetyBlocked` before general `SafetyError`; record its public reason
   and return a refusal (CLI exit 2). A local interactive runner that explicitly
   opted into notifications can call `notify_stop(root, now, local_notification)`
   when handling a guard refusal. Do not put this side effect inside imports or
   the generic guard. Historical confirmation logging, exports, state inspection
   and explicit reconciliation must remain available without this new-work gate.
5. Connect an explicit user-invoked `resume-stop` or safety `reconcile` command to
   `resume_stop(root, originating_run_id, now, evidence, command)` (`reconcile`
   is an equivalent function). Evidence must contain non-empty `operator` and
   `note` strings describing the operator's review. The parent must only supply
   these from the actual explicit operator command, never automatically or from
   listing text. Remove STOP first; its presence still forbids reconciliation.
   Removing STOP by itself, choosing another run ID, or passing `resume-stop` as
   the guard's command never clears an incident. The resolution is preserved in
   the incident ledger. Start subsequent work with a new run ID so the original
   stopped run remains an accurate historical record.

## Python-free seen hook

The parent should place this shell-only refusal before any lookup. It uses shell
builtins and preserves a pending record even if STOP is removed before Python
next runs. It neither invokes Python nor sends notifications:

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

On the next Python command, call `guard` before dispatching new work, using the
same run ID retained by the runner (`APPLYD_RUN_ID` or an explicit CLI argument).
This materializes the incident and run record; a pending marker itself blocks
work even after STOP removal. A shell-only caller cannot deliver a notification
or write the full timestamped run JSON; the owning runner must handle the refusal
and invoke the local Python guard/notification path. Do not claim shell refusal
alone delivered a notification. At startup the Python guard also checks the
incident ledger and restores a missing sentinel after an interrupted write.
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

All these artifacts stay private under the existing publication deny rule. Public
logs/notifications may contain the stable stop reason and opaque IDs, never the
private incident dict, screenshot path/contents, page text or URL query tokens.
The parent must separately allowlist only the three generic implementation files.

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
