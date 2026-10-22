# Durable next-action runner

`scripts/runner.py` is a local, standard-library controller. It reads validated
current settings and the existing immutable settings run, derives unique quotas
using `batches`, and writes only `applications/runs/<id>/state.json`. It never
creates or replaces `run.json`. There is no browser implementation or service.

The parent must create a settings run once with `settings.run_start`, or resume
the existing ID with `settings.load_run`. Do not reset a run to replenish limits.
Import the runner at command dispatch, after workspace helpers are defined.

```python
from runner import Runner, Hooks, Queue, Observation, Verification, Kind

runner = Runner(root, run_id, hooks=hooks, adapter=adapter)
action = runner.next_action(now)             # one typed plan, no browser action
results = runner.execute(clock=clock, max_steps=3)  # bounded synchronous calls
```

`now` and every `clock()` result must have an explicit UTC offset; use the current
settings timezone. `max_steps` is an integer from 0 to 100, defaults to 1, and
bounds adapter calls. Each call is synchronous and must itself be bounded by the
parent/browser timeout. There is no background loop, sleeping, or scheduling.
Each execution boundary reloads the queue, settings and original run.

`Action` is immutable and contains exactly `kind`, `run_id`, `sequence`,
`application_id`, `record_ref`, `remaining`, `reason`. `as_dict()` produces JSON.
Kinds are `discover`, `prepare`, `review`, `submit`, `manual_handoff`, `complete`.
The adapter receives **only this action**, never the queue, full listing,
profile, answer bank, resume bytes, or a whole run snapshot. It can request
task-specific content through the parent's existing helpers using the references.
Keep observations equally small: `Observation(evidence_ref=None, failure=None)`;
`failure`, when present, is an existing `retry_policy.Reason`.

Missing callbacks or adapter returns `manual_handoff` with `callbacks_missing` or
`adapter_missing`. No missing implementation is treated as execution. Without
callbacks, nothing is read or persisted because no writer lock is available.
With callbacks but no adapter, the candidate plan and manual result are saved;
no pending action or verified checkpoint is manufactured. `save_only` and
`dry_run` settings currently return `mode_requires_manual_handoff`; neither can
reach Submit through this runner.

## Required parent callbacks

All seven `Hooks` fields are required. The runner holds `writer()` throughout
selection, recovery, live gates, the adapter call, verification and persistence.
Callbacks must use the **already held** lock and must not reacquire it or dispatch
CLI commands which would acquire it. The parent remains responsible for exports.

1. `writer() -> ContextManager`: return the existing `workspace.writer(root)`.
   All browser writers for this workspace/session must share it. Never supply a
   no-op context manager. Do not start a second browser writer from the adapter.
2. `recover(run_id, now) -> None`: call `workspace.recover_submit(root, now)` and
   persist/export its recovery when needed. Recover claims before selecting work;
   never clear an uncertain intent automatically. This hook handles persistence,
   while the guard below enforces new-work refusals. It must allow historical
   confirmation verification even after caps, deadlines or safety stops change.
3. `queue(run_id, now) -> Queue(records, batch_id, target, category_targets=None)`:
   supply **all** workspace records via `workspace.records(root)`, including old
   batches and aliases. Read the selected existing batch brief for its ID/target
   and categories; do not put a target in the adapter or hardcode a new target.
   Run the existing answer-change/requeue workflow before returning records when
   answers have changed. Only `workspace.requeue_answers` may establish scoped,
   fresh answer resolution and persist `resumed_from_answers=True`; do not infer
   resolution from a cleared UI question. Retry limits use `work_budget.max_retries`, taking the minimum current, frozen and saved budget; three is the default.
4. `guard(action, now) -> None`: raise on any refusal; only `None` succeeds.
   Immediately before **every** adapter call, call `workspace.work_guard` and
   `workspace.browser_retry_guard`, plus `retry_guard` for an application.
   Enforce both current and original frozen policy/scope and listing eligibility
   with `require_eligible`; enforce current answer and resume resolution/freshness
   through the existing preparation/resolver helpers. Do not turn a truthy
   eligibility dictionary into approval: require `decision == "eligible"`.
   Require current job identity and all normal review/preflight gates for Submit.
   The adapter must also detect safety challenges from each current-page snapshot
   and call the existing stop workflow before any subsequent interaction. A
   runner boundary does not replace per-click browser gates.
5. `begin_submit(action, now) -> bool`: freshly observe the current job URL and
   call **`workspace.begin_submit(root, action.application_id, observed_url,
   session_id, now)`**, under the held writer. Return exactly `True` only when
   its `click_authorized` field is exactly true. This existing helper performs
   preflight/reviewer, settings, identity, duplicate, safety and durable browser
   claim checks. The runner invokes it after persisting pending, immediately
   before the adapter's one Submit click. No alternative click authorization is
   supported. A refusal or exception prevents the adapter call and retains pending.
6. `verify(action, observation_or_none, now) -> Verification`: independently
   inspect saved/current evidence; validate it and persist the resulting workspace
   transition **before returning**. On restart the observation is `None`: inspect
   durable results rather than repeating the browser operation. This callback must
   be idempotent. Return one of:
   - `Verification("verified", evidence_ref)`: the action actually completed.
     Prepare must have durably moved the record to ready, or persisted actual unknown-question dependencies in awaiting_answer; review must have called
     `workspace.review_form` on independently observed fields/attachment. Submit
     must have observed confirmation and called existing `workspace.event` with
     status `submitted`, saved evidence and actual occurrence time. The runner
     additionally requires an evidence-backed confirmation in batch accounting.
   - `Verification("not_executed", evidence_ref)`: evidence proves the operation
     did not occur. Reconcile an uncertain Submit through the existing evidence
     **and note** event path first, preserving retry counts and audit history.
     This clears pending without advancing the verified checkpoint. Future work
     must still pass every normal gate; it grants no immediate click permission.
   - `Verification("unresolved")`: no proof either way; keep pending and hand off.
   The evidence reference must name a nonempty existing file inside workspace
   `applications/`; traversal and escaping symlinks are refused. A reference alone
   is not proof: the parent must validate its bytes, identity and meaning.
7. `failure(action, reason, now) -> None`: persist the existing typed failure
   workflow (`workspace.retry_failure`, or its existing uncertainty event path),
   including retry counters/session incidents and exports. Submit failures always
   use `submission_uncertain`. Unclassified non-submit exceptions use
   `unsupported`, never guessed timeout semantics. Failure does not release runner
   pending. If this callback commits then export fails, the runner does not call
   it a second time for the same observation. Filesystem/callback errors fail
   closed; repair exports with the existing recovery workflow, never by replay.

The adapter's `prepare` handles one bounded ordinary preparation/form unit;
`review` obtains the independent current form snapshot; `submit` performs one
authorized click and captures its result; `discover` captures a bounded set of
new listing headers/records through existing helpers. Unsupported tools/layouts
must produce a failure or unresolved verification, not a fabricated completion.
The runner does not infer answers, resume scope, eligibility or UI observations.

`Hooks.native_guard(action, now) -> None` is an optional stricter native-boundary
hook, defaulting to `guard`. It runs after every durable outer or nested tool
reservation, immediately before native invocation, with nested telemetry disabled.
Policy/budget checks run fresh first. An integration whose ordinary guard refuses
all uncertain submissions must supply this hook to validate the exact claim owned
by the current invocation after `begin_submit`. It must preserve that intent on
refusal and never grant a retry or reconcile automatically. The workspace hook
rechecks claim identity/ownership, submission snapshot/review/freshness, policy,
writer ownership and STOP without recovering the active claim. URL observation
also uses a fresh guard after its browser span reservation. Adapters with nested
native callbacks must invoke them through the supplied `boundary.call`.

## Checkpoints, queue and quotas

State keeps `plan`, `pending` and the last verified `checkpoint` separately.
Planning never advances a checkpoint. Pending is fsynced before any adapter call,
including discovery, and before invoking begin-submit. Process exit, provider
errors, verification failure or a failed checkpoint write retain pending. A
restart must verify the saved operation or prove non-execution before selecting
another browser action. Removing a claim, changing a status, or seeing a stale
ready page cannot release pending. Unknown actions are never automatically replayed.

After verification, selection uses durable workspace statuses: prepare eligible
records, review ready records without a saved review, submit reviewed ready
records. The last checkpoint records the independently verified action/evidence;
the workspace record owns the actual application stage. No in-memory stage cursor
must survive process exit. `execute` returns verified actions or a typed handoff/
completion. `not_executed` verification ends execution with a handoff, not success.

Resolved records precede other eligible queued records; IDs break ties. Queued
work precedes discovery. A confirmed or uncertain member excludes every alias,
including transitive cross-post bridges and empty unfinished intents. Unresolved
answer waits/backoff/exhaustion stay in durable `decisions`; independent eligible
records can continue. Active session incidents still block through the parent.

Batch progress uses `batches.batch_progress`: only unique evidence-backed
confirmations earn quota credit, attributed to their original saved batch.
The selected brief is frozen in runner state on first planning; subsequent target
or category changes may tighten it, never enlarge it. Changing batch ID requires
a new run. Current and frozen settings caps/deadlines also apply. Remaining quota
is the smallest batch/daily/run allowance; category allowances additionally filter
candidates. No mutable success counter is trusted. A late confirmation can still
be verified after a deadline/stop; new work then stops at the next policy check.
The controller reuses existing internal batch grouping/settings counting helpers
to keep deduplication and cap semantics identical, rather than duplicating them.

`runner_workspace.build_runner` connects these callbacks to the existing workspace gates, exact artifact hashes, durable answer requeue, independent saved review and confirmation events. `plan_next(now)` returns a plan without requiring an execution adapter; it does not reserve a click or claim an executed action.

The CLI reads an explicitly selected JSON batch brief with `batch_id`, integer `target`, optional `schema_version` and optional `category_targets`. It never changes the original brief. Example:

```json
{"batch_id":"example-batch","target":3,"category_targets":{}}
```

Create the configured run with `workspace.py run-start --id example-run`, then use `workspace.py next-action --run-id example-run --batch <private-brief.json>`. `workspace.py run --run-id example-run --batch <private-brief.json> --steps 3` executes only through a supplied runtime adapter; the default CLI reports `adapter_missing` and performs no browser action. Programmatic runtimes supply an adapter callable plus `observe_url(action)` for fresh Submit identity. There is no bundled authenticated browser driver. Unknown questions are a verified local preparation checkpoint that leaves the job waiting and permits independent jobs to continue. Missing, stale or unverified evidence retains pending and requires reconciliation.

CLI dispatch lets the controller own its writer lock and rebuilds dirty exports separately; callbacks never reacquire that lock. Run only focused synthetic verification during this integration:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_runner*.py' -v
```
