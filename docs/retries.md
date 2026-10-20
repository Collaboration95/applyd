# Retry policy

`scripts/retry_policy.py` uses only the Python standard library. It performs no
filesystem, network, browser, environment or clock reads. All transition APIs
return a deep copy of the supplied record. They preserve unrelated fields and
do not change application status or perform an action. The parent owns locks,
atomic persistence, settings, evidence validation, CLI integration and exports.

## Parent API

```python
next_retry_state(record, reason, now, max_retries=3, backoff_seconds=60,
                 *, blocked_answer_ids=None) -> dict
check_retry(record, now, max_retries=3) -> RetryDecision
resume_retry(record, blocker_id, evidence_id, now, max_retries=3) -> dict
resolve_answer_blocker(record, resolved_answer_ids, now, max_retries=3) -> dict
```

`reason` is a `Reason` member or its exact string value. Accepted codes are
`unknown_answer`, `login`, `page_changed`, `throttling`, `timeout`,
`submission_uncertain`, `off_scope`, `account_safety`, `manual_stop`, `unsupported`.
Never pass raw provider errors as reasons. Invalid input raises `ValueError`
with static text; errors do not interpolate supplied text. The module does not
scrub unrelated record fields: the parent must never insert raw provider/page
text into retry fields or public reports.

`now` accepts an aware `datetime` or ISO timestamp with a UTC offset. Pass the
workspace's configured timezone. Persisted timestamps retain explicit offsets;
backoff measures elapsed time correctly across daylight-saving changes. Limits
are non-negative integers; backoff is a positive integer number of seconds.

The transition APIs initialize and return these durable fields (save the full
returned record, rather than reconstructing these fields on restart):

```json
{
  "retry_policy_version": 1,
  "retry_budget": 3,
  "retry_counts": {"timeout": 1},
  "blocker": {
    "id": "retry-1",
    "reason": "timeout",
    "blocked_at": "2026-10-20T09:00:00+08:00",
    "answer_ids": [],
    "resume": null
  },
  "next_eligible_at": "2026-10-20T09:01:00+08:00"
}
```

`blocker` and `next_eligible_at` may be null. Blocker IDs advance with recorded
failure counts. An unknown-answer transition also unions its IDs into the
existing `blocked_on_answer_ids` application field. These IDs survive a later
login/page-change stop. Answer resolution removes only verified resolved IDs
from both locations. The parent must persist these changes together with its
answer-inbox updates, under the same writer lock. An answer blocker requires
actual IDs, supplied explicitly or read from `record.blocked_on_answer_ids`.
The API takes no answer values or question text.

`check_retry` returns the immutable `RetryDecision` dataclass. Its `.as_dict()`
is JSON-serializable and contains exactly:

```json
{
  "decision": "backoff",
  "reason": "timeout",
  "next_eligible_at": "2026-10-20T09:01:00+08:00",
  "browser_stopped": false
}
```

`decision` is a `Decision` member/string: `eligible`, `backoff`, `waiting_answer`,
`resume_required`, `reconciliation_required`, `exhausted`, `confirmed`, `stopped`.
`reason` is a `Reason` or null. `next_eligible_at` is non-null only for the
`backoff` decision; an exhausted state can retain its historical deadline in the
record. Eligibility permits consideration of work; it never authorizes Submit.
`check_retry` is read-only and does not freeze defaults into a fresh record.
Malformed/incomplete saved retry state fails closed; do not delete it to reset
the policy. Keep these records private.

## Budget and waiting

The first observed failure freezes `retry_budget`. Every transition uses the
minimum of the saved budget and current `max_retries`, persisting a tighter
budget. Checks use that same minimum without writing. A settings increase never
raises the frozen budget. Restart with the saved fields, not a new record.

Timeout, throttling, login and page-change failures share one budget; their
individual counts remain in `retry_counts`. With budget 3, failures 1, 2 and 3
permit a retry, and failure 4 exhausts the budget. Budget 0 permits initial work
but no retry following a failure. Changing reason does not replenish the budget.

Transient failure N sets a deadline using
`min(3600, backoff_seconds * 2**(N-1))`, where N is the combined budgeted failure
count. Delays are capped at one hour, and eligibility begins exactly at the
deadline. Calling `next_retry_state` during an active backoff does not count
another failure or extend the deadline. After eligibility, call it only for a
new observed failure. Polling belongs in `check_retry`, never a failure loop.

Unknown answers have no timed retry. Repeated observations union dependencies
without incrementing an active answer wait. Call `resolve_answer_blocker` only
after the parent resolver verifies current, scoped, fresh confirmed facts for
the supplied canonical IDs. Unrelated/partial resolutions keep waiting. Resolving
all IDs clears the answer blocker; any other stop or backoff remains effective.
New answer facts can legitimately requeue work repeatedly: answer waits neither
consume nor reset the failure budget. The parent schedules checks on answer
changes, without polling unresolved jobs indefinitely.

## Browser stops and submission protection

Login and page change set a durable blocker with `browser_stopped: true` in the
decision. Time passing or changing an application status cannot resume it.
A new browser stop takes precedence over an answer wait or transient backoff;
an unexpired transient deadline remains effective after explicit browser resume.
`resume_retry` requires the current blocker ID and an opaque evidence ID. The
parent must validate and privately save evidence of explicit operator resume
for that incident before calling it. The ID itself is not proof. A successful
resume preserves counts/budget and records this exact nested value:

```json
{"resume": {"evidence_id": "evidence-1", "at": "2026-10-20T09:02:00+08:00"}}
```

Stale incident IDs, empty evidence, evidence before the stop and exhausted
budgets cannot resume. `off_scope` and `unsupported` stop only the affected
work. `account_safety` and `manual_stop` also report a browser stop; their
resolution belongs to the existing safety workflow, not this resume API. A
login/page-change observation cannot downgrade a safety stop.

Confirmed `submissions`, `submitted_at`, or a submitted/confirmed status always
deny retries, including after a stale status restore. A `submission_uncertain`
status, matching `stop_reason`, unfinished non-null `submission_intent`
(including an empty object), or a persisted uncertainty blocker always returns
`reconciliation_required`. Recording a timeout for such a record creates an
uncertainty blocker instead of a transient retry. The blocker survives later
status changes or intent removal. These guards take precedence over budgets and
caller retryable-status settings. Neither resume nor answer resolution can
clear uncertainty. The parent must reconcile through its existing evidence-based
workflow and explicitly clear the uncertainty blocker only after reconciliation;
it must preserve counts, budget and the reconciliation audit trail.

## Parent integration order

1. Under the existing writer lock, load the application and any active session
   browser-stop records. Recover unfinished submit intents before selecting work.
   Pass current `max_retries` on every call.
2. On one observed failure, classify it into a static `Reason`, call
   `next_retry_state`, and atomically save the returned copy before further work.
   Mirror a browser stop into the durable session/run state, or scan persisted
   active browser stops at startup and before browser actions. A per-application
   check alone cannot stop browser work on another application/session.
3. Explicit resume must use the same incident/evidence in both session and
   application state. Persist the returned state before restarting browser work.
   Existing safety guards remain mandatory and independent.
4. After verified answer changes, call `resolve_answer_blocker` and persist.
   This can remove answer dependencies while retaining a browser stop.
5. Call `check_retry` before selecting an application. Continue independent
   eligible jobs during another job's answer wait, transient backoff, uncertainty
   or exhaustion. An active session browser stop blocks all browser actions in
   that session; independent non-browser work can continue.
6. Before Submit, additionally enforce existing application status/settings,
   safety, answer/resume freshness, job identity, duplicate, single-writer and
   durable submission-intent gates. This module never clicks, reserves an
   attempt, changes status or validates external evidence.

## Workspace CLI and persistence

The CLI integration is in `scripts/workspace.py`. The current integration uses
`work_budget.max_retries`, default **3**, through the minimum current/frozen/saved limit. Every workspace retry boundary uses that limit; increasing settings cannot replenish a saved budget. Frozen `run.json` bytes remain unchanged.

```sh
python3 scripts/workspace.py retry-failure --id app-example --reason timeout
python3 scripts/workspace.py retry-status --id app-example
python3 scripts/workspace.py retry-resume --id app-example --blocker-id retry-1 --evidence /path/to/resume-evidence.txt
python3 scripts/workspace.py browser-guard --run-id run-example --id app-example
```

`retry-failure` records one newly observed failure, not a poll. Active waits are
idempotent. `retry-status` is read-only: it reports application eligibility,
counts, the frozen budget, actual unresolved question IDs, and all current
session browser incidents. Neither command clicks, sleeps, schedules a retry,
or prepares another frozen attempt. `event --status failed --reason timeout`
uses the same transition. An unclassified failed event stops as `unsupported`;
free-form notes never classify failures. Evidence-backed reconciliation of an
uncertain attempt is a separate operation and does not count as a new failure.

CLI mutations hold the workspace writer lock through record save, index update
and export. Record JSON is atomically replaced and fsynced. A durable pending
export marker is written before record/index changes, so interruption or an
index/export error cannot make stale reports look clean. These are serialized
atomic file replacements, not a transaction across multiple filesystem files.
If interrupted, preserve the authoritative record, rebuild the index with
`reindex` if needed, then run `export`. Export recovery does not replay the
failure or consume another retry. `--defer-export` keeps the normal deferred
export contract. Python callers must hold `writer(root)` when mutating a shared
workspace; CLI commands already do this.

## Browser integration

Call `browser-guard` immediately before each adapter/browser action, with `--id`
when acting on an application. A successful guard permits consideration of that
action; Submit additionally requires the existing preflight/begin-submit gates.
The guard recovers unfinished browser claims to durable uncertainty before
allowing new browser work. Existing safety/manual-stop gates remain first.

Login/page-change incidents are stored as `record.browser_stop`, independently
of the retry blocker. Browser boundaries scan persisted records across all runs
in this workspace's single-writer browser session. A stop on one application
therefore also stops another application's browser actions, including after a
process restart or a new run. A later uncertainty/confirmation cannot erase the
incident. Older records with a login/page-change retry blocker are recognized
without migration. Malformed saved state fails closed.

The same gates protect `work_guard`, answer preparation, form review, preparing
and ready events, preflight, scope checks and tab registration. Preflight also
checks the current durable application state when passed an older in-memory
snapshot. A `preparing` event during an answer wait returns a `waiting_answer`
no-op and leaves the application waiting; it cannot grant execution or ready.

Capture stores already observed listing facts and can continue as non-browser
work. Its initialized duplicate lookup does **not** scan application records.
Capture success never permits navigation; the browser adapter must use the
browser guard before its next action. No raw UI/browser capability can enforce
this protocol on an adapter that bypasses the CLI gates.

## Explicit incident resume

Use the `blocker_id` for the matching application in `retry-status.browser_stops`.
`retry-resume` is the explicit operator operation; it requires that exact current
incident and a readable, nonempty evidence file. It saves the exact evidence
bytes under the application's private `retry-evidence/` directory, named by
SHA-256, before linking them from an audit event and the incident. The resume
timestamp cannot predate the incident. Missing/blank evidence, stale IDs, and
repeated resumes fail. Browser guards verify the saved hash and incident-bound
audit event; deleting/changing evidence fails closed.

For a retryable login/page-change blocker the command uses `resume_retry` and
preserves its counts, budget and any transient deadline. A session incident can
also be explicitly cleared after its application is confirmed, uncertain,
terminally stopped or exhausted. This clears **only the browser incident** for
independent work: the application's confirmation/uncertainty/finite-budget
decision still denies retry, and `resume_retry` itself still rejects these
states. Multiple pending incidents each require their own explicit resume.
Account-safety/manual stops remain subject to `resume-stop`, not this operation.

Historical confirmation and outcome logging bypass new-work gates and preserve
the exact prepared attempt even after a deadline, stop or resume replacement.
A prepared application's late submitted/uncertain observation may be recorded
after a failure. Negative uncertainty reconciliation still requires saved,
nonempty evidence and a note. Only this reconciliation path clears an uncertainty
blocker; it retains its incident in the audit event and never resets counts,
budget, other browser incidents, or a frozen attempt.

## Answer-driven requeue

Answer preparation persists the resolver's actual canonical unresolved question
IDs in both `blocked_on_answer_ids` and the typed answer blocker. It does not
replace them with a generic `unknown_answer` dependency. Requeue processes both
partial and complete resolver results, removing only IDs the current scoped,
fresh resolver confirmed. IDs missing from the saved form, expired facts and
facts scoped to another application remain unresolved. The CLI accepts no list
of user-asserted resolved IDs.

Resolved IDs can be persisted while another retry/browser gate remains active.
Only records that pass all answer, resume, eligibility and retry gates enter the
runnable list. Answer changes preserve finite failure counts and browser stops;
they never clear uncertainty. The answer import and its requeue execute under
the same CLI writer lock.

Synthetic verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_retry_policy.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_retry_integration.py -v
```
