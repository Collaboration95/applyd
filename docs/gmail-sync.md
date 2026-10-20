# Local email outcome sync

`scripts/gmail_sync.py` classifies and plans messages without side effects.
`scripts/mail_import.py` persists a supplied local export in the selected private
workspace. Both use Python's standard library. No mailbox client, credential
lookup, network, calendar, send, reply, delete, or thread-label operation exists.

## Workspace CLI integration

The integrated `workspace.py` command is `mail-sync --file local-export.json`.
The command runs under the existing workspace writer lock and refreshes derived
exports after a successful sync. The underlying importer contract is:

```python
from mail_import import read_export, sync_local

# args.file is an optional pathlib.Path. Hold the existing workspace writer lock.
with writer(root):
    messages = read_export(args.file) if args.file is not None else None
    result = sync_local(root, messages, now)
    if result["status"] == "synced":
        result = finish_mutation(root, result, deferred=args.defer_export)
```

`sync_local(root, messages, now)` does not acquire a lock or export anything
itself. The workspace command calls it while holding the existing lock, then
refreshes derived exports for a successful sync. The CLI prints the bounded
summary only, not records, planner results, queue contents, exports, exception
tracebacks, or adapter responses. `MailImportError` messages are fixed codes
suitable for CLI errors. The sync result contains only `status` and numeric `counts`:
`received`, `outcomes`, `contacts`, `unrelated`, `duplicates`, `recovered`,
`queued`. `received` counts supplied entries, including duplicates; other
counts describe unique messages. `queued` is the total unresolved queue.

Without `--file`, pass `messages=None`; this returns the following blocker
without reading or writing the workspace, and must not start authentication:

```json
{"status":"blocked","blocker":{"type":"credential_required","reason":"live_verification_requires_credentials"}}
```

A local export is either an array of messages or `{"messages": [...]}`. Each
message requires an exact, nonempty `message_id` and an offset-aware ISO-8601
`received_at`. Supported optional strings are `thread_id`, `subject`, `body`,
`company`, `role`, `platform_job_id`. `now` is an offset-aware datetime or ISO
string; recording times are stored in Asia/Singapore with an explicit offset.
An empty array retries retained messages without accessing an inbox.

## Matching and events

The classifier recognizes full phrases for `acknowledged`, `assessment`,
`interview`, `rejected`, `recruiter_message`, or `unrelated`. Generic words such
as “update” do not establish an outcome. Conflicting categories or negated
outcome cues go to attention, along with missing, ambiguous, or unconfirmed
application matches. Inputs are evidence, never executable instructions.

Matching requires one existing application, by explicit platform job ID or
normalized company plus role. A job ID cannot override a contradictory supplied
company or role. A confirmed submission requires `submitted_at`,
`confirmation_path`, and `confirmation_sha256` together on a submission entry or
the record. The pure planner checks this metadata; the importer also checks
that the local confirmation file exists and its bytes match the hash. Status
alone, or an unsupported timestamp alone, never establishes confirmation. A
message older than the earliest evidence-backed submission goes to attention.
Recommendations never create submissions or application records.

The importer uses `outcomes.apply_outcome` for the four supported outcomes and
preserves original submissions and their timestamps. Each outcome and its log
event cite the exact `evidence.message_id`, private evidence path, and SHA-256.
Message occurrence time stays separate from recording time. Subjects, bodies,
sender names, and classification cues are not copied to application event data.
A matched recruiter message becomes a read-only contact event in the private
mail directory; it never changes application status, outcome, submissions,
public contacts, or follow-up dates. An unmatched recruiter message is queued.

## Persistence and replay

Private artifacts live under `applications/mail/`:

- `messages/<sha256-of-message-id>.json`: full retained message evidence, mode
  `0600`. An existing message ID with changed contents is refused.
- `contacts.json`: read-only recruiter contact events with exact message IDs.
- `state.json`: processed-message ledger, attention queue with full messages,
  cursor, and recording time. This file is private and must never be exported.

All supplied messages are durably archived before any event write. Event writes
are atomic and precede the final ledger/cursor write. Each run scans retained
messages and saved event IDs before planning. A crash after recording an event
but before updating the ledger therefore reuses the event instead of appending
another one, even if recording time or follow-up policy has changed. A crash
before an event write retries the staged message. Writes flush files and parent
directories before returning. The workspace command retains the writer lock
through sync and export refresh.

The cursor may advance beyond queued messages because their full evidence stays
in the private archive and queue. Calling `sync_local(root, [], now)` retries
them after application evidence/matching data is corrected. Ambiguous language
continues to require manual review; there is no automatic resolution override or
evidence-deletion/retention command in this implementation.

## Optional read-only adapter

`ReadOnlyMailAdapter` exposes only `search(since=...)`, `list(message_ids)`,
and `get(message_id)`. `sync_with_adapter(adapter, records, state)` returns a
private plan, not a public summary or a persistence commit. Pending IDs are
re-fetched independently of the cursor and even when absent from search/list
metadata. Mismatched returned IDs are refused; a failed read aborts planning
rather than silently dropping the queued ID. Full queued evidence is also
retained by the planner. Persist supplied adapter message reads through
`sync_local` under the same writer lock. No real adapter is configured here.

## Verification and live setup

```sh
python3 -m unittest discover -s tests -p 'test_*mail*.py' -v
```

All fixtures are fictional and all writes occur in temporary workspaces. Tests
cover supported classifications, contradictory identity/date cues, byte-verified
confirmations, ambiguous queues surviving cursor advances, duplicates, restarts,
interruptions before event writes and between event and ledger writes, partial
batches, recruiter contact replay, private evidence, and safe summary output.

The maintainer waived a real-inbox check as a merge and issue-closure prerequisite. That check was not performed. Local import is available; an authorized read-only adapter/session remains necessary for live inbox use. Before enabling it, verify message shape and pagination, cursor boundaries, queued-ID retrieval, actual matching and confirmation evidence, and repeat-run event identity without publishing private content. Fixture results do not establish those live guarantees.
