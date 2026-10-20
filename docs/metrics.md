# Effort measurement

The runner writes private telemetry to `applications/runs/<run_id>/metrics.json`
inside the selected workspace, under the existing writer lock. It uses Python's
standard library and performs no provider calls. Application records, frozen
answer/resume copies and original `run.json` snapshots are unchanged by metrics.
Do not publish the ledger: its run/application IDs are private.

An event reserves the runner action sequence before `begin_submit` or the
adapter call. Its fixed stage is `discover`, `prepare`, `review` or `submit`.
Reservation time is recorded with the Asia/Singapore offset for period selection,
never for calculating duration. A reservation consumes its sequence even after
failure, refusal or reconciliation; planning alone creates no event.

Only the adapter invocation is timed with `time.monotonic`. Guards, submit
identity/authorization checks, persistence, verification, manual waiting and
shared discovery allocation are outside individual application effort. A normal
exception has a measured duration and a fixed failure reason; exception text is
never stored. A process interruption leaves `status="reserved"`, duration and
usage `null`. Recovery can change verification disposition but cannot reconstruct
duration or provider use. A logging failure returns `metrics_unavailable`,
preserves pending work and does not mark an application failed; the runner
reconciles evidence instead of replaying an action to collect telemetry.

Retry reason/count comes from the parent's existing typed blocker and durable
per-reason failure counter. The same blocker counter is recorded only once,
including across runner restarts/runs, so moving from prepare to review does not
invent another retry. The aggregate `retries[].count` counts **reserved retry
actions**, not historical failures or proof every reserved call completed.
`observed_actions` and `observed_attempted_applications` require an adapter result
or ordinary exception. Interrupted calls without an observed completion remain
reservations of unknown execution status. `incomplete_actions` includes missing
durations and pending/unresolved verification.

Answer reuse counts actual snapshot rows with `resolution="resolved"`, a
non-null answer and a saved `source_answer_id`. False and zero are answers.
Unknown, expired, unresolved or manually supplied rows without a saved source
are excluded. The workspace hook reads the current preparation snapshot after
verification and stores only the count; it never estimates from a bank size,
question count or default. Missing measurement remains `null`, distinct from a
measured zero. Repeated preparations count the actual rows reused each time.

Provider measurements come exclusively from `Observation.usage`, validated by
`budgets.validate_usage`: measured per-call `tokens` and/or `cost`, with no
provider/model labels. Omitted fields remain `null`; reported zero is measured
zero. No price lookup, allocation, inference or currency conversion occurs.
`provider_usage` totals cover **recorded reservations only** and a field is
available only when every selected reservation reports it. `provider_coverage`
contains measured action counts and explicitly partial measured subtotals.
These are not the full cost of an application, an uninstrumented historical
run, or all work in a week. Legacy records do not become zero-cost records.

## Public Python interfaces

```python
load_events(root) -> list[dict]
summarize(records, events, *, start=None, end=None) -> dict
known_answer_reuse(rows) -> int
Runner(root, run_id, *, hooks=None, adapter=None, monotonic=None)
build_runner(root, run_id, brief_file, adapter=None, *, monotonic=None)
```

Import the first three from `metrics`; import the constructors from `runner`
and `runner_workspace`. `load_events` reads only separate metrics ledgers;
absent historical telemetry yields no events. Corrupt, duplicate or escaping
ledgers fail closed with static errors. Its returned events are private.
`summarize` is pure and always aggregate-only: fixed labels and numbers/null,
without source IDs, timestamps, URLs, filenames, question text, company names
or arbitrary groups. Caller-supplied records use `batches` transitive identity
and evidence-backed first confirmation semantics. Source ordering does not
change results. Optional aware `[start, end)` bounds select first confirmations
and reservations; effort for that confirmation cohort includes earlier measured
stages up to its first confirmation.

`reserved_applications` counts distinct local application IDs with reservations;
`observed_attempted_applications` counts those with observed adapter completions.
`unique_confirmed_applications` counts transitive job identities with saved
confirmation evidence. These denominators intentionally differ. Discovery has
no application ID and is included only in stage/provider totals.

The median includes unique confirmed applications only when preparation,
review and submit each have a verified observed stage, and every recorded
reservation for that identity up to first confirmation has a measured duration.
Failed measured attempts are included; missing/interrupted stages exclude that
application from the median. `confirmed_with_complete_effort`,
`confirmed_with_partial_effort` and `confirmed_without_effort` expose coverage.
This is measured adapter effort with complete recorded stage coverage, not total
human time or proof of measurement before telemetry existed.

`Hooks.answer_reuse(action) -> int | None` is an optional trailing hook, called
only after verified preparation under the writer. Existing positional hooks
remain compatible. `build_runner` supplies the exact snapshot count hook.
`monotonic` is an optional zero-argument seconds clock for synthetic tests and
defaults to `time.monotonic`; the existing `execute(clock=...)` wall clock still
controls policy deadlines and event times. Never persist monotonic clock origins
or compare them across processes.

Low-level runner hooks in `metrics` are `reserve(root, run_id, sequence, stage,
application_id, now, *, retry_reason=None, retry_count=0)`, `observe(root, run_id,
sequence, duration_seconds, *, usage=None, failure_reason=None, failed=False)`
and `reconcile(root, run_id, sequence, disposition, *, answer_reuse=None)`.
The caller must hold the workspace writer. Repeated observations cannot replace
measurements. Legacy pending sequences without telemetry remain absent.

Parent CLI wiring:

```python
import metrics
import reports

loaded = metrics.load_events(root)
weekly = reports.weekly_report(records, week, timezone=timezone,
                               shareable=shareable, metrics=loaded)
summary = metrics.summarize(records, loaded)  # optional aggregate metrics command
```

The parent owns argument parsing, workspace selection and publication allowlists.
The CLI must not print loaded private events as a shareable summary.

Focused verification uses synthetic temporary workspaces only:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_metrics.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_reports.py -v
```
