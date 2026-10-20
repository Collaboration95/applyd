# Draft-only follow-ups

Outreach creates email drafts through an injected draft-only adapter, or saves a
LinkedIn note for explicit user copying. It never sends, replies, invites,
navigates to profiles or discovers contacts. Implementation verification uses
only temporary synthetic workspaces and fake adapters.

## Settings and runs

`settings.json` accepts this default-off policy:

```json
"outreach": {
  "enabled": false,
  "business_days": 10,
  "daily_draft_cap": 3,
  "do_not_contact": []
}
```

`outreach.validate_policy` strictly validates these fields. Days and cap are
non-negative integers, including zero; boolean numbers and unknown fields fail.
Settings reject null policies. A missing policy receives disabled defaults.
Existing immutable run snapshots retain their original bytes and raw hash;
missing outreach in an older frozen snapshot means disabled.

Start a separate run with `run-start --id <outreach-run>` after configuring the
policy. Creation requires that existing run: both current and frozen policies
must enable outreach, both modes must be `apply`, the longer wait and smaller
cap apply, and do-not-contact entries are combined. Both timezones' daily caps
and due times apply. Either elapsed deadline blocks, including its exact boundary.
The wrapper invokes `workspace.work_guard` for STOP, safety and browser recovery.
Do not reuse an application's original submission run or reset a run to relax
restrictions. Outreach stores its own `run_id` on the reservation and never
reattaches the application, prepared resume or submitted history to that run.

## Workspace command hooks

Import `outreach_workspace` inside the workspace CLI. Hold `workspace.writer(root)`
through each call, including the adapter operation and export:

```python
import outreach_workspace as outreach

result = outreach.create_follow_up(
    root, run_id, application_id, contact_id, channel,
    project_id, bullet_id, now, adapter=None, deferred=False,
)
result = outreach.reconcile_follow_up(
    root, application_id, follow_up_id, now,
    resolution="not_created", evidence=saved_evidence, note="User verified absence.",
    adapter_id=None, deferred=False,
)
result = outreach.confirm_manual_send(
    root, application_id, follow_up_id,
    occurred_at=actual_send_time, recorded_at=now,
    evidence=saved_user_confirmation, deferred=False,
)
```

Creation maps to `outreach --run-id --id --contact-id --channel --project-id
--bullet-id`. Reconciliation requires `--id --follow-up-id --resolution --evidence
--note`, plus an actual `--adapter-id` for `draft_created`. Manual-send logging
requires `--id --follow-up-id --at --evidence`; parse `--at` as an aware timestamp
and pass it as `occurred_at`. Recording time is separate. No reconciliation or
manual-send hook invokes an adapter or restarts a run; observed history remains
recordable after a deadline or stop.

The wrapper lazily imports workspace helpers to avoid circular imports. It reloads
authoritative records for each helper snapshot and uses `persist_record` for the
durable export-dirty marker, atomic record replacement and index update.
`finish_mutation` refreshes exports after mutations, including adapter uncertainty
and failed completion writes. Mutation and export outcomes remain separate; an
export failure never permits another draft operation. Deferred export uses the
existing recovery mechanism. Blocked creation returns IDs, status, reason and
daily count; a follow-up ID exists only for an actual durable reservation.

## Sources and eligibility

Only saved contacts with visible-fact sources, capture times and `verified: false`
are eligible. No address guessing or enrichment is permitted. Email requires a
saved address; LinkedIn requires an absolute HTTP(S) LinkedIn `/in/` or `/pub/`
profile URL without credentials. Do-not-contact matches exact case-insensitive,
trimmed application IDs, company, contact ID/name/email/profile or saved listing
URL. Explicit record/contact `do_not_contact: true` also blocks.

The wrapper reads the current catalog once to select one exact active bullet.
Its project fingerprint and bullet ID must match a confirmed
`submissions[].selected_projects[].content_hash` and `bullet_ids`. The exact
submitted resume and confirmation must still exist inside the workspace and
match their saved SHA-256 hashes. Prepared or newly selected resumes are never
substituted. Missing, changed or unverifiable original-PDF project provenance
returns `unverifiable_project_source`; no claim is invented or rewritten.

`safe_evidence(root, reference, expected_sha256=None)` returns `{path, sha256}`
for an existing nonempty file, with a workspace-relative path. It rejects
traversal, external absolute paths, escaping symlinks, missing/empty files and
expected-hash mismatches. Reconciliation and manual sends require actual saved
evidence; reconciliation also requires a nonempty note. Evidence hashes are
stored with those observations. This helper never copies or creates evidence.

The core connects cross-post aliases transitively using existing application,
platform/job, requisition, canonical URL and company/title/location identities.
Saved reservation identity keys survive later alias changes. Responses,
withdrawals, outcomes and uncertain submissions block the group. One actual
follow-up is allowed across channels. Due dates advance weekdays at the local
submission time; submission day is day zero and holidays are not modeled.

## Adapter, recovery and dashboard

The optional adapter exposes only:

```python
adapter.create_draft(to=saved_email, subject=subject, body=text,
                     reservation_id=follow_up_id) -> actual_provider_draft_id
```

No live email adapter or credential acquisition is included. `adapter=None` for
email persists `outreach_blocker.reason: credentials_required`, with no
reservation or fake draft ID. The helper durably reserves `draft_uncertain`
before calling an adapter. An actual nonempty provider ID yields `draft_created`;
timeout, exception, missing ID, interruption or completion-write failure leaves
uncertainty. No automatic retry is allowed. Outstanding uncertainty consumes a
quota slot on every day until evidence-backed reconciliation.

LinkedIn uses the same reservation workflow but saves only `note_created` and
calls no adapter. Its template is `Following up on {role}. {exact_point} Thanks!`.
A note above 200 UTF-16 units blocks without truncation. Successful notes/drafts
count on their reservation date. Explicit `not_created` reconciliation releases
the slot and permits a new reservation ID while retaining the original audit.
A confirmed manual send cannot be reconciled as absent.

The private dashboard JSON and classic-script snapshot include structured
`outreach` and `outreach_blocker`. Details render text through text nodes, show
draft/note state separately from manual-send confirmation, and provide a safe
LinkedIn profile link and an explicit Copy note button. Rendering never copies,
opens links or sends anything. Oversized imported notes have no copy action.
Follow-ups do not change application status, submission times or run grouping.
Contacts, exact text and provenance remain private export data.

## Focused verification

```sh
python3 -B -m unittest discover -s tests -p 'test_outreach*.py' -v
python3 -B -m unittest discover -s tests -p 'test_settings*.py' -v
node --test tests/outreach.test.cjs tests/contacts.test.cjs tests/control-plane.test.cjs
```
