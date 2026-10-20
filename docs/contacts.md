# Visible application contacts

`scripts/contacts.py` implements the pure contact helpers for issue #22. It uses
only the Python standard library. It performs no file reads/writes, browser
actions, profile visits, network requests, enrichment, or contact discovery.
All tests use fictional, in-memory facts.

## Normalized contact

Each entry in a record's `contacts` list contains:

| Field | Contract |
| --- | --- |
| `contact_id` | `contact-` plus SHA-256 of canonical JSON of exact `name`, `headline`, `profile_url`, `email`, and `source`. |
| `name`, `headline`, `profile_url`, `email` | Exact supplied visible strings, or null. Missing optional facts become null. At least one must be non-null. |
| `source` | Required: `linkedin_hiring_team`, `jd_text`, or `confirmation_email`. Other sources are rejected. |
| `captured_at` | ISO-8601 timestamp with seconds and explicit UTC offset (`Z` accepted). Missing/null uses the caller's `now`. |
| `relationship` | `job_poster`, `hiring_team`, or `unknown`. Generic validation defaults to `unknown`. |
| `verified` | Always the boolean `false`. Incoming true, null, numbers, or strings are rejected. No verification operation is provided. |

Unknown contact fields are rejected. Profile links must be absolute HTTP(S)
URLs without credentials, whitespace, control characters, or backslashes. Links
are preserved and never visited. Strings retain whitespace and case; no missing
identity or relationship is inferred from a name, title, domain, or profile URL.
The caller must supply only visible facts: validation cannot establish whether
a name/headline/profile link actually appeared in a source.

An email must match a complete printed address exactly, including case, in
caller-supplied text. `jd_text` contacts require `jd_text` evidence;
`confirmation_email` contacts require `email_text` evidence. A hiring-team
contact with an email requires either text. A substring of another address,
obfuscated address, guessed email, or differently cased address is insufficient.
The helpers do not extract email addresses or infer which person owns them.
The current extractor's card cannot supply an email; normally create a separate
`jd_text` or `confirmation_email` entry when printed email evidence exists.

## Integration interfaces

```python
from contacts import (
    ContactError, add_contact, contacts_json, from_hiring_team, remove_contact,
    validate_contact_inputs, validate_contacts,
)
from listing_input import normalize_listing_contacts
```

- `from_hiring_team(card, now) -> list[dict]`: pass the existing extractor's
  `hiring_team` object (`name`, `headline`, `profile_url`), or null. Absent,
  empty, and all-null cards produce `[]`. A visible card produces one contact
  with source `linkedin_hiring_team`, relationship `hiring_team`, and no email.
  Optional explicit `relationship` uses the enum; no headline-based job-poster
  classification occurs. Other card keys are rejected.
- `validate_contacts(contacts, now, jd_text=None, email_text=None) -> list[dict]`:
  returns new normalized dictionaries; null produces `[]`. All entries are
  validated before any result is returned. Duplicate identities retain the first
  entry and its original capture time. Supplied IDs must match the derived ID.
- `validate_contact_inputs(contacts, jd_text=None, email_text=None) -> list[dict]`:
  performs the same checks and normalization, except missing/null capture times
  remain null. This supports strict input validation without reading a clock or
  inventing a timestamp. These objects are not ready for persistence. Explicit
  timestamps must still be valid. `validate_listing` uses this function only
  when the optional top-level `contacts` field is supplied.
- `contacts_json(contacts) -> str`: serializes a list as compact JSON with sorted
  object keys and literal Unicode. Null becomes `[]`. It does not revalidate or
  alter historical contacts. Use the result as one field in the existing CSV
  writer; do not interpolate CSV manually.
- `add_contact(record, contact, now, jd_text=None, email_text=None) -> dict`:
  validates one incoming contact and returns a deep copy of the record. Appends
  only when its ID is absent. Existing contacts, capture/submission timestamps,
  verification values, events, and unrelated record keys remain unchanged.
  Historical contacts are not revalidated or enriched; their source evidence
  does not need to be reloaded. This operation cannot update a contact.
- `remove_contact(record, contact_id) -> dict`: returns a deep copy with only
  the exact ID removed. Missing IDs are harmless; records without `contacts`
  retain that shape. No other record fields change.

`now` accepts an aware `datetime` or an offset-bearing ISO string; it is used
only for missing/null capture times. A naive/missing `now` fails when needed.
No wall clock is read. IDs exclude time, relationship, and verification;
changing any visible identity field or source produces a distinct ID.
This is exact identity deduplication, not cross-source person matching.

`ContactError` subclasses `ValueError`, with `field`, `reason`, and `exit_code=2`.
For capture, wrap as `ListingError(exc.field, exc.reason)` and exit 2 before
creating directories, descriptions, indexes, records, or exports.

### Capture and listing input

`validate_listing(data)` now accepts optional top-level `contacts` (list or
null), rejects unsupported fields/sources and invalid values using the existing
helper, and preserves the existing `hiring_team` schema unchanged. Missing
capture times in explicit contact inputs stay null at this stage. No contact
source is added to the listing's top-level source values. The extractor already
returns a supported visible `hiring_team` card; its output needs no change.

The parent must import `normalize_listing_contacts` into `workspace.py` and
call it inside `capture(root, data, now, ...)`, after copying/checking input but
before any filesystem write (including index creation, description writes,
directory creation, record persistence, or export). This also covers direct
internal callers; changing only the CLI stdin/file branch is insufficient:

```python
data = normalize_listing_contacts(data, now)
```

This pure function deep-copies the input; validates the supplied `contacts`
value independently (including falsy invalid values); converts the visible card
with source `linkedin_hiring_team`, relationship `hiring_team`, and the capture
operation's `now`; and merges/deduplicates the contacts. Card contacts come first.
Explicit contact timestamps are preserved except where a duplicate card identity
wins. No-card inputs persist `contacts: []`. The original `hiring_team` object
is retained for compatibility. Failures are wrapped as field-specific
`ListingError` with exit code 2. The helper has no filesystem or wall-clock access.

Keep the existing CLI call to `validate_listing` for both `--stdin` and `--file`.
Persist the resulting `contacts` along with the other normalized record fields;
do not discard it in a record allowlist. Allow `contacts` in any parent-owned
publication/export schema or privacy checker that enumerates record fields.

No top-level raw confirmation-email field is required for listing capture.
The parent email-sync/CLI caller can provide its already available confirmation
text via the `email_text` argument and preserve its existing evidence reference.
An incoming email without the corresponding supplied text must fail; matching
a `source` label alone is insufficient. Do not load private email from here.

### CLI, export, and dashboard

The integrated `workspace.py contact --id ... --add <contact.json>` and `--remove <contact-id>` commands load the record under the writer lock, validate the requested change and refresh exports after durable persistence. Additions use already available JD text or an explicitly supplied confirmation source via `--email-evidence`. Removal matches one normalized contact ID. There is no verification flag.

`export_dashboard` preserves `contacts` as structured objects in both JSON and
the classic-script snapshot. The details dialog displays the visible facts,
source, exact capture timestamp, relationship, contact ID and unverified state.
Historical records show "No saved contacts." All facts use text nodes. Profile
links require absolute HTTP(S) URLs without credentials, whitespace, controls or
backslashes; unsafe imported URLs are displayed as plain text. No contact links
are visited, and emails remain text.

The integrated CSV writer stores a `contacts` column using stable JSON before ordinary CSV quoting and formula escaping:

```python
row["contacts"] = contacts_json(record.get("contacts"))
```

Capture normalization, the CSV column, add/remove commands and the explicit publication allowlists are integrated. Contact validation does not change application submission history or invent missing facts.

## Verification

```sh
python3 -B -m unittest discover -s tests -p test_contacts.py -v
python3 -B -m unittest discover -s tests -p test_contacts_integration.py -v
python3 -B -m unittest discover -s tests -p test_listing_input.py -v
node --test tests/extractor.test.cjs tests/contacts.test.cjs tests/control-plane.test.cjs
```

Coverage includes visible/no-card/partial cards, forbidden sources and metadata,
exact printed-email evidence, unsafe URLs, offset timestamps, immutable false
verification, deterministic IDs, retries, and deep-copy add/remove behavior that
preserves submitted records. Integration coverage includes strict listing input,
deferred versus actual capture time, extractor/card compatibility, invalid-source
stdin rejection without capture artifacts, structured dashboard exports, CSV
JSON round trips, safe DOM rendering and stale-detail clearing. Positive `capture --stdin`, CSV round trips and contact commands are covered by `test_contacts_cli.py`; `test_contacts_integration.py` checks strict normalization and serialization. No real
employer or private workspace is used.
