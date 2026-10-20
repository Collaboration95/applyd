# Run work budgets

`work_budget` is an optional strict schema-1 settings object. Partial objects
receive these defaults:

```json
{"max_actions":100,"max_retries":3,"provider_token_cap":null,"provider_cost_cap":null}
```

Action/retry limits and token caps are non-negative integers; cost caps are
non-negative finite numbers. Booleans, unknown keys and negative/non-finite
numbers fail validation. Only provider caps accept `null` (disabled). A zero
action or provider cap prevents the first action. Zero retries permits the
original attempt but no failure-driven retry, using the existing retry policy.

The runner intersects current and verified frozen settings at selection and
again after its mandatory parent guard. It freshly checks deadline, unique
batch/daily/run quota, retries and work budget before reserving any call. Submit
gets another check after `begin_submit` and immediately before the adapter call.
The run's original start controls time limits; restarting does not renew them.
An exhausted record remains excluded while independent records may continue.

## Durable accounting and handoff

Inside the existing writer lock, `scripts/budgets.py` atomically persists
`applications/runs/<run_id>/budget.json`. Each adapter invocation reserves one
sequence **before** the call (and before `begin_submit`). Discovery, preparation,
review and Submit all count. Failures, crashes, denied Submit claims and later
proof of non-execution do not refund a reservation. Invocation `max_steps` still
bounds each call to `execute`; it cannot reset the durable action count.

`state.json` continues to own pending, plan and independently verified checkpoint.
`run.json` remains immutable, with its original raw settings hash. Legacy frozen
settings without `work_budget` receive compatible defaults during validation;
their saved bytes/hash are unchanged. If previously executed runner state lacks
its budget file, new work returns `budget_state_missing` instead of starting a
fresh counter. Restore the matching saved budget; do not delete state to reset it.
Malformed or escaping artifacts fail closed.

Budget stops return `manual_handoff` with a static `reason` and relative
`budget_ref`; the saved plan or pending state retains restart context. Reasons
include `action_budget_reached`, `provider_budget_reached`,
`provider_usage_unavailable` and `budget_state_missing`, alongside existing
deadline/quota/retry reasons. Ordinary adapter actions retain their minimal
reference-only shape; no profile, job prose or answer values enter this file.

## Measured provider usage

An adapter may return `Observation(usage={"tokens": 12, "cost": 0.002})` with
explicitly measured **per-invocation deltas**. Tokens must be non-negative
integers; cost must be a non-negative finite number in the same unit as the cost
cap. This module performs no currency conversion or price lookup. Unknown keys,
boolean numbers, estimates and nonnumeric fields are rejected. The adapter is
responsible for sourcing measurements from actual provider metadata.

Omitted usage/metrics remain `null`. A cumulative metric is available only if
every reserved action reports it; a later measurement cannot fill an earlier
gap. A reported zero is measured zero. A run with no calls reports both metrics
as `null`, while the gate knows no calls have yet consumed work.

A positive provider cap permits the first invocation. Thereafter missing usage
for a configured metric yields `provider_usage_unavailable`, retaining the
verified checkpoint. Complete measured totals at/above a cap stop the next call.
These are checks of observed consumption at action boundaries, not a promise
that the next unknown-cost call cannot exceed the cap. No pre-call cost estimate
is invented. With no provider cap, unknown usage does not block work; deadlines,
quotas and action/retry limits still apply.

## Submit interruption

Pending and its budget reservation are durable before the Submit claim. A crash
or interruption after the claim preserves both the claim and pending operation.
Exceptions use the existing `submission_uncertain` failure path. Restart first
recovers claims and independently verifies evidence, even after a deadline or
cap is reached. Unresolved pending never replays. Observed confirmation can
advance the checkpoint after a stop; a new action then fails the fresh gates.
Only evidence-backed reconciliation can prove non-execution, without refunding
the consumed action or retry count. If the deadline expires during Submit
authorization, the runner refuses the click and retains uncertainty for review.

## Retry-policy integration

`budgets.retry_limit(root, record)` is public, read-only and returns the minimum
of current `work_budget.max_retries`, the record's verified frozen run policy
(including `prepared.run_id`), and its saved `retry_budget`. It never increases
an existing saved budget. A record without a run uses current/saved limits.

`workspace.py` imports this helper through its `retry_limit` wrapper and passes
the resulting limit to retry checks and state transitions, including failure,
resume, answer-blocker and uncertainty paths. `retry_guard(record, now,
root=None)` uses the configured helper when given a workspace root and retains
the legacy `MAX_RETRIES` fallback for standalone callers without one.
`runner_workspace.py` supplies the helper's result directly to
`retry_policy.check_retry`; `runner.py` also intersects it with the frozen
run's work-budget limit. Settings validation and run snapshots remain in place.

Focused verification (synthetic fixtures and fake clocks only):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_budgets.py -v
```
