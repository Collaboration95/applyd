# Batch accounting

`scripts/batches.py` is a read-only helper over records supplied by the caller.
It does not load private files, persist state, change an index, or authorize a
browser action. The parent CLI owns record loading, locking, event integration,
and dashboard export. There is no mutable quota counter.

## Parent integration

1. At ready preparation, call
   `attach_attempt_context(snapshot, batch_id, category=None)`. It returns a deep
   copy containing both fields. A batch ID and category are non-empty strings;
   `None` supports unassigned legacy work. A category requires a batch ID.
   Changing context already present on a snapshot raises `ValueError`; construct
   a fresh attempt instead. Preserve the fields by copying this prepared snapshot
   into the confirmed `submissions` entry. Never fill historical context from
   current top-level fields.
2. `validate_event_time(value, field="occurred_at")` returns an aware `datetime`.
   It accepts an ISO timestamp with an explicit offset (including `Z`) or an
   aware `datetime`; it never supplies a missing timezone.
   `event_metadata(occurred_at, recorded_at)` returns ISO strings for `at`,
   `occurred_at`, and `recorded_at`. `at` equals `occurred_at` for existing
   consumers. Future occurrences relative to recording are rejected.
3. For a delayed confirmation, write the actual occurrence to `submitted_at` in
   both the saved submission and the record, plus the returned event metadata.
   `recorded_at` is the current logging time. Keep status update/recording times
   separate. Later outcomes must preserve original submission history. Export
   the saved submission time and saved resume so the one-hour run grouping uses
   occurrence time. This module does not implement dashboard grouping.
4. Call `batch_progress(records, batch_id, target, category_targets=None)` with
   the complete record collection, including prior batches and cross-posts.
   Targets are non-negative integers, not booleans. The result contains
   `batch_id`, `target`, `confirmed`, `remaining`, `complete`, `categories`,
   `confirmed_application_ids`, and `exclusions`. Each requested category has
   `target`, `confirmed`, `remaining`, and `complete`. Remaining values stop at
   zero. Overall completion requires the total and all category targets.
5. `exclusions` has only `confirmed` and `submission_uncertain` application-ID
   arrays, across all batches. All aliases of an excluded job are included.
   A live `submission_intent` is uncertain even if a stale status says failed.
   Confirmations take precedence over uncertainty. Awaiting/failed/discovered
   entries never become permanent exclusions just because an index saw them.
6. `retryable_application_ids(records, settings)` proposes IDs with a status in
   `settings["retryable_statuses"]` (defaults from `settings.py`), no saved answer
   blockers, and no confirmed/uncertain alias. The parent must first recompute
   and persist resolved blockers using the existing answer workflow. A proposal
   does not check resume freshness, current listing eligibility, retry budgets,
   or preflight. Repeat those gates before execution. It does not deduplicate
   proposed aliases or select the next action; the runner owns scheduling.

## Confirmation and identity rules

A saved submission counts only when it has `submitted_at`, a submitted status
(omitted status defaults to submitted), and `confirmation_path` or non-empty
`evidence`. The parent has already observed and saved this evidence; the pure
helper does not open or authenticate artifacts. A status or timestamp alone does
not count. Follow-up statuses on the record do not remove saved confirmations.
Known submission timestamps must include offsets. When present, `occurred_at`
is the actual confirmation time; otherwise `submitted_at` is used.

Identity matching imports the existing settings identity and seen-index text
normalization helpers and lazily uses `workspace.canonical_url`; URL normalization
is not duplicated. Canonical LinkedIn IDs also match sparse platform-ID records. Application
IDs, platform/job IDs, company/requisition IDs, canonical listing URLs, and
company/role/location link records transitively. Unconfirmed records can bridge
two cross-posts. Every record needs an application ID. Incomplete legacy headers
can still match by the available identities.

Each connected job earns at most one quota credit across all batches, attributed
to its earliest evidence-backed confirmation's saved `batch_id` and `category`.
This is independent of record order, later attempts, and edited top-level batch
or category fields. Missing original batch context receives no batch credit.
Conflicting batches at the same earliest instant receive no credit; conflicting
categories within one batch receive total credit but no category credit. The
job remains excluded in either case. The reported counted ID is the
lexicographically first alias, for deterministic output.

A legacy top-level `submitted_at` with evidence can exclude the job but cannot
assign a batch. An older legacy confirmation also prevents a later duplicate
from earning a new batch credit. Preserve the complete history when migrating.

Verification uses only synthetic in-memory records:

```sh
python3 -m unittest discover -s tests -p test_batches.py -v
```

CLI: `workspace.py event --status ready ... --batch-id <batch> --category <category>` freezes context on the new attempt. `workspace.py batch-progress --batch-id <batch> --target <count>` derives progress from all records. `--at` on events records the actual occurrence separately from the logging time; use an explicit UTC offset.
