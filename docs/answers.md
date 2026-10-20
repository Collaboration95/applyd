# Deterministic answer core

`scripts/answers.py` is standard-library-only code. It uses no model, browser,
network, application status writer, or dashboard exporter. All examples here are
synthetic. `root` always means the selected **private workspace**, not a public
release directory. Returned payloads contain private facts and must not enter
public telemetry, release assets, GitHub, or shared logs.

## Integration contract

```python
from answers import prepare_answers, validate_snapshot, context, import_answers, requeue

prepared = prepare_answers(root, record, questions, now)
# Persist prepared["blocked_on_answer_ids"] on this application.
# If nonempty, persist status="awaiting_answer"; continue independent applications.
# Otherwise the result says "preparing": it does not grant ready or submit.

validation = validate_snapshot(root, record, prepared["answers"], now)
if not validation["valid"]:
    raise ValueError("Answer snapshot needs review")
```

`now` is an offset-aware `datetime` or ISO-8601 timestamp; naive values fail. The
caller supplies configured local time (Asia/Singapore where configured). These
functions do not read wall time, filesystem dates, or PDF metadata. `ValueError`
indicates invalid input; filesystem failures propagate. No error requires logging
the input facts.

The parent CLI holds its existing workspace writer lock around `prepare_answers`,
`import_answers`, `context`, and any persistence of `requeue` results. The core does
not take a second, potentially deadlocking lock. Parent-owned `event ready` and
`preflight` must both call `validate_snapshot` against the actual exact answer
file, reject `valid=false`, and retain their existing resume and file hash checks.
The browser adapter must compare current wording/options/units/scope with the
prepared questions before filling or submitting: this module cannot see a changed
live form. Persist status and blockers before exporting a refreshed dashboard.

Each public call loads `profile/answers.json` once. Preparation sorts rows by
occurrence ID. Equal inputs produce equal bytes; advancing `now` without crossing
a validity boundary also preserves bytes. The original form order is not used as
an identity. Snapshots are never rewritten: new answers produce a new path.

## Answer bank schema (version 1)

The authoritative file is `profile/answers.json`:

```json
{
  "schema_version": 1,
  "documented_skills": ["Python"],
  "preferences": {},
  "answers": [
    {
      "id": "python-experience",
      "version": 1,
      "value": 1,
      "type": "number",
      "kind": "experience_duration",
      "skill": "Python",
      "units": "years",
      "scope": {"country": "SG"},
      "basis": "saved_answer",
      "aliases": ["Python experience?", "Years using Python?"],
      "source": "Synthetic user confirmation",
      "confirmed_at": "2026-10-19T12:00:00+08:00",
      "valid_until": "2026-11-01T12:00:00+08:00"
    }
  ]
}
```

Source IDs are stable identifiers, not question wording. Versions are nonempty
strings or integers; if omitted, the whole source record's SHA-256 is its version.
Every snapshot also keeps the whole-record fingerprint, so changing value, scope,
alias, confirmation, or deadline without bumping an explicit version still fails
validation. `value: null` is an unknown fact and is valid storage, never a usable
answer. Missing confirmation/source is unusable. Future confirmation/effective
dates and expired `valid_until` or `review_due_at` block use; expiry is inclusive.
Optional `effective_from`, `valid_until`, and `review_due_at` use offset timestamps.

Supported types are `text`, `number`, `boolean`, `single_select`, and
`multi_select`. There is no string/number/boolean coercion: `true` is not `1`, and
`true` does not select `"Yes"`. A source string can select one uniquely normalized
string option, and the snapshot retains the exact observed option text.
Multiselect values are nonempty lists with no repeated selections. No numeric
unit or currency conversion occurs. Empty text and invalid options are unusable.

`units` is null, a nonempty string such as `"years"`, or an object containing only
`unit`, `currency`, and/or `period` strings. Units must match exactly. Numerical
salary sources require both currency and period. Experience duration sources
require explicit units. `options` is a list of distinct JSON scalar choices;
case/whitespace-equivalent string choices are ambiguous and rejected. Select
types require nonempty options. Option order does not affect alias matching.

`scope` is an object containing only `country`, `company`, `application_id`, and/or
`consent_id`, with nonempty string values. `{}` (or the compatibility spelling
`"global"`) explicitly permits general reuse. Scope restrictions must match
application/question context. Values normalize Unicode, case and whitespace;
country codes and country names are **not** interchangeable unless saved that way.
The caller supplies `record.country`; location strings are never parsed to guess
a country. Question scope that conflicts with known application identity/company/
country fails matching. Unknown scope keys and descriptive free-text scopes are
rejected; they are not interpreted as authorization.

Kinds are `fact` (default), `experience_duration`, `routine_preference`,
`legal_authorization`, `sponsorship`, `residency`, `consent`, `salary`, `language`,
and `qualification`. Legal authorization, sponsorship, and residency sources
require explicit country scopes. Consent requires either an application scope or
both company and consent-notice scopes. These kinds match separately; a source
for one declaration cannot answer another. Consent never becomes a global fact.
Adapters must supply the appropriate kind and jurisdiction/notice context; the
core does not classify unfamiliar legal wording.

`aliases` is the only mapping authority. Each entry is an approved exact string
or an object with `question`, and optional `type`, `options`, `units`, `scope`,
`kind`, `skill`. Missing descriptor fields inherit source fields. Alias objects
may narrow source scope but cannot broaden it or change kind, type, or units.
Matching uses NFKC + case folding + whitespace collapsing only: punctuation,
synonyms, translations and similar wording are not guessed. Exact field type,
option set, units, skill and compatible scope must also match. Multiple matching
sources remain ambiguous; record order never breaks a tie.

`basis` is `saved_answer` (default), `authorized_default`, or
`employer_preference`. Defaults require `authorized: true` saved in the bank.
`authorized_default` applies only to explicitly mapped experience-duration fields
whose `skill` occurs in the bank's `documented_skills`. There is no built-in
one-year rule, inferred CV skill list, or adjustment to satisfy employer minimums.
`employer_preference` applies only to explicitly mapped `routine_preference`
entries: its `value` is the allowed list of exact options. The current question's
`employer_preferred_option` must be one of those allowed values. A matching saved
explicit answer takes precedence, even when null, malformed, or expired; it does
not silently fall through to a default. Multiple explicit matches are unresolved.

## Exact questions, snapshots, and inbox

`prepare_answers(root, record, questions, now)` accepts a list:

```json
[
  {
    "question": "Years using Python?",
    "field_id": "python-years",
    "type": "number",
    "kind": "experience_duration",
    "skill": "Python",
    "units": "years",
    "scope": {"country": "SG"},
    "required": true
  }
]
```

`question` is required; omitted `type`, `kind`, `options`, `units`, `scope`, and
`required` default to `text`, `fact`, `[]`, null, `{}`, and true. The only other
question keys are `skill`, `field_id`, and `employer_preferred_option`.
Identical repeated fields need distinct stable `field_id` values; positional IDs
are forbidden. `record.application_id` is required; `company`, `country`,
`listing_url`, `canonical_listing_url`, and `destination_url` (or
`application_url`) provide occurrence context. No input is mutated.

Canonical `q-…` IDs hash the stable source ID when an explicit mapping exists.
Unmapped IDs hash normalized wording, type, option set, units, kind, skill and
scope, including known application country; unmapped consent is application
scoped. Canonical IDs survive reordering/restarts. Explicit alias additions may
link a previously unknown canonical item to a source; occurrence identity stays
unchanged. `occ-…` IDs hash application ID plus the complete exact field descriptor,
including `field_id`, without list position or the selected source. Changed
wording/options are new exact occurrences. Different employers share a canonical
item only within the applicable scope; their occurrences remain separate.

The result contains:

- `answers`: exact rows, with original descriptor fields, `application_id`,
  `question_id`, `occurrence_id`, `answer`, and `resolution`.
- Resolved/matched-source rows also contain `source_answer_id`, `source_version`,
  `source_fingerprint`, `source_value`, `source_scope`, `basis`, `source`, and
  `confirmed_at`. Thus exact selected option text and saved source value are both
  retained. Unmatched rows have `answer: null` and no invented provenance.
- `answers_path`: immutable `applications/records/<app>/answers/<hash>.json` list;
  `snapshot_version` hashes that list, and `bank_version` versions current facts.
- `blocked_on_answer_ids` and `unresolved_ids`: sorted canonical IDs;
  `ready`: answer completeness only; `status`: `awaiting_answer` or `preparing`.
- `inbox_path`: `profile/question-inbox.json`.

The inbox is a versioned object with `questions`: sorted canonical groups
containing `question_id`, `status`, `application_ids`, and exact `occurrences`.
Each occurrence retains the full row, company, source/destination/canonical
listing URLs and a workspace-relative saved `record_path`. Preparing a current
form replaces only that application's active inbox occurrences. Historical
snapshots remain intact. This is private export input, not a public release file.

Unknown fields, including unknown optional fields supplied for answering, block
preparation conservatively. Callers can omit optional fields they deliberately
leave blank; they must not pass null through ready. Repreparing a form produces a
new snapshot only when content changes. The snapshot files and generated private
JSON files use mode 0600. Symlink/path escapes are rejected.

`validate_snapshot(root, record, answers, now)` returns
`{"valid": bool, "errors": [...], "legacy_manual": bool}`. Each error has a stable
`code`, `question_id`, and `occurrence_id`. It re-resolves against one fresh bank
load and compares the full expected row, including current source fingerprint,
version/value, confirmation, scope, expiry, type, options, units and basis. It
rejects nulls, duplicate occurrences, and omissions from the current prepared
inbox. No frozen file is edited. An empty list is valid only when no prepared
occurrences are missing (a form can have no answer fields).

### Compatibility boundary

Existing bank records without a schema version are readable. Missing aliases do
not imply any mapping, missing scope is global only for compatible general facts,
and free-text scopes require a reviewed structured replacement. Old source
records without an explicit version use the content version. Nothing in the
legacy bank is rewritten by preparation or context generation.

Legacy answer lists with only `question`/`answer` cannot prove source freshness.
By default they fail `missing_provenance`. An explicit
`validate_snapshot(..., allow_legacy=True)` audit/compatibility policy permits
nonnull manual rows and labels the result `legacy_manual=true`; a partial modern
snapshot cannot use this escape hatch. Null is always rejected, including in
legacy optional fields. Do not use this opt-in as proof of freshness for a new
autonomous ready/preflight transition. Historical submitted files remain exactly
as saved; importing reviewed source facts and preparing new snapshots is the safe
upgrade path. No automatic migration or fabricated provenance is performed.

## Compact context

`context(root, now)` returns and writes `profile/context.json`, containing
`schema_version`, `version`, `bank_version`, compact source `answers`,
`preferences`, resume-release metadata, and awaiting-answer application/blocker
IDs. The return also includes `path`. Preferences come from optional
`profile/preferences.json`, falling back to bank `preferences`. Answer versions,
values, scopes, deadlines and usability are included; aliases are represented by
`bank_version`. Resume bytes/content and application history are not included.
Submit-time resume resolution remains mandatory.

The version is a content hash without a generated-at timestamp. Answer updates,
expiry boundaries, preferences, resume metadata and active blocker changes affect
it. Unrelated events, notes, submission log text and export timestamps do not.
The caller can retain this compact result for the run and refresh after saved
updates; this API does not maintain a long-lived process cache.

## Batch imports and persisted truth

`import_answers(root, change, now, preview=False)` accepts exactly this envelope:

```json
{
  "schema_version": 1,
  "import_id": "synthetic-import-1",
  "base_version": "<bank_version from refreshed private context>",
  "changes": [
    {
      "change_id": "synthetic-change-1",
      "answer": {
        "id": "synthetic-language",
        "version": 1,
        "value": "None",
        "scope": {"country": "SG"},
        "type": "text",
        "kind": "language",
        "aliases": ["Synthetic language proficiency?"],
        "source": "Synthetic user review",
        "confirmed_at": "2026-10-20T12:00:00+08:00"
      }
    }
  ]
}
```

Changes are **complete source replacements**, not patches. Imported sources require
`id`, `value`, `scope`, `type`, `aliases`, `source`, and `confirmed_at`; supported
optional source fields are those documented above. Unknown keys fail. IDs use
letters/digits followed by letters/digits/underscore/dot/hyphen. Duplicate source
IDs or change IDs within a batch fail. Changing an explicitly versioned source
requires a new version, or omitting the version to use a content version. Expired,
malformed, invalid-option, incompatible-scope, future-confirmation and
unauthorized-default entries fail before any write. Null may be saved as unknown.

With `preview=True`, validation and affected-job resolution run without writes.
Results include `persisted=false`, proposed `bank_version`, `applied_change_ids`,
and `affected_jobs` with old/new blockers and answer completeness. A preview is
not authorization to run a job. Records that never captured questions cannot have
their impact determined here. Submitted/uncertain records may appear in the
preview for visibility, but are never changed or automatically requeued.

Without preview, one atomic bank-file replacement commits the whole batch plus
private `imports` and `applied_changes` ledgers. No record/inbox/context/export is
changed inside that transaction. Malformed or stale-base batches make no partial
write. A repeated import ID with identical content returns the persisted receipt;
different content fails. Repeated change IDs with identical content are no-ops;
different content fails. Ledger-only changes do not alter `bank_version`.
Duplicate-import results report current `bank_version` plus the original
`imported_bank_version` if the bank has since changed.

The parent implements the manual CLI import under its lock, then refreshes
context, requeue state, and the private dashboard. A downloaded/exported change
file remains pending until the CLI reports `persisted=true` and refreshed
authoritative data confirms the saved values. Never label a browser download or
preview as saved. This module deliberately provides no separate command parser
or HTTP save endpoint.

## Requeue proposals and execution gates

`requeue(root, now, resume_check=None, eligibility_check=None)` reads the bank once,
re-resolves captured inbox forms and returns `updates`, `runnable_ids`, `waiting`,
and `untouched_ids`. Only `awaiting_answer` records with no recorded submissions
are considered. Submitted, uncertain and all other statuses remain untouched.
Every captured question must resolve, and blockers absent from the captured form
remain blockers. Resolving one of two blockers never unlocks a job.

Each callback takes `(root, record, now)` and must return exactly `True`.
`resume_check` must invoke current resume resolution/freshness checks;
`eligibility_check` must check the current listing against current saved policy.
Missing callbacks, false/nonboolean results, or validation/filesystem failures
leave the job waiting with typed reasons. No default assumes resume freshness or
listing eligibility. Callback record arguments are copies.

Successful proposals say `status="preparing"`, clear answer blockers, and set
`execution_checks_required=true`. The parent persists these proposals and must
repeat resume/eligibility checks immediately before execution, then prepare a
fresh snapshot and validate at ready/preflight. The old immutable snapshot is
never repurposed after an import. Requeue itself writes no records or exports and
cannot submit or retry an uncertain application.

## Verification

```sh
python3 -m unittest discover -s tests -p test_answers.py -v
python3 -m unittest discover -s tests -v
node --test tests/run-grouping.test.cjs
```

Fixtures use temporary synthetic workspaces only. These checks do not read a
real profile, contact an employer, prove browser capability, or replace parent
CLI/export integration tests.
