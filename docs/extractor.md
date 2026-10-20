# LinkedIn listing capture

Use the already authenticated browser and a private workspace. The extractor
reads only the current job details DOM. It does not click, scroll, expand text,
change the page, navigate, visit profiles, or make network requests. Extraction
does not apply to the job.

1. Check the job identity before opening a new listing:

   ```sh
   APPLYD_WORKSPACE=/path/to/private-workspace scripts/seen.sh 900001
   ```

   `900001 new` means no indexed record exists. Existing records require the
   normal status/retry decision; do not recapture just to bypass that decision.

2. Open that job in the browser. Wait for the selected job details and full
   description to be available. Inspect the page normally if expansion is needed;
   the snippet itself cannot obtain unloaded content.

3. Evaluate the **entire** contents of `scripts/extract_linkedin.js` in that page.
   Its IIFE returns a JSON string. Transfer the returned string verbatim through
   the browser tool's result/clipboard, without a Markdown fence or an extra JSON
   string wrapper. Keep it private. No hand-written `tmp/job-input-*.json` is
   needed.

4. Pipe the exact result to capture. On macOS, after copying the returned JSON:

   ```sh
   pbpaste | python3 scripts/workspace.py --workspace /path/to/private-workspace capture --stdin
   ```

   A browser adapter can pipe the same JSON bytes directly to that command.
   Do not pipe browser logs or a quoted serialization of the JSON string.
   Capture validates the entire object before writing an application record,
   description, index marker, or export. A validation failure reports the field
   and exits 2; it must not leave a partially captured listing. `--stdin` and
   `--file` are alternative inputs to the same validator.

## Extracted data

Required facts are `platform_job_id`, `title`, `company`, `location`,
`job_description`, and `easy_apply`. `platform` is `linkedin` and `source` is
`linkedin_job_page`. Identity must agree between the current URL, selected details
panel and job links when they contain a job ID. The numeric job ID is normalized
to `https://www.linkedin.com/jobs/view/<id>` in `canonical_url`. `source_url`
retains the actual current page URL, including search parameters.

Optional visible facts are `workplace_type`, `employment_type`, `posted_age`,
`apply_url` and `hiring_team` (`name`, `headline`, `profile_url`). They remain
null when unavailable; a hidden hiring card is not read. Employment/workplace
values retain their visible wording. No date is inferred from posted-age text.
No profile link is followed. The job description retains rendered DOM text and
paragraph breaks; the reader cannot certify content that the page has not loaded.

For Easy Apply, `apply_url` is null and `destination_url` is the canonical job
URL. For external Apply, both URLs are the visible control's HTTP(S) `href`.
If an Apply button exposes no href, both stay null. The extractor never follows
redirects or invents an employer URL. An absent/unrecognized Apply control is an
`easy_apply` error, not a guessed false value.

Selector fallbacks are centralized in `SELECTORS` in the snippet. Supported
synthetic layouts cover the current unified details panel, an alternate search
details panel and a guest-style external job. A layout that no longer supplies
required facts stops with `ListingError`; update selectors with a synthetic
regression fixture rather than guessing values. This is fixture verification,
not a claim of live LinkedIn compatibility testing.

## Integration API

```python
from listing_input import ListingError, validate_listing

# Call before capture creates directories, indexes, descriptions or exports.
normalized = validate_listing(data)
# Then invoke the existing capture helper with normalized.
```

`validate_listing(data) -> dict` is pure, uses no workspace/profile files, and
returns a new dict. It accepts the extracted object and legacy capture fields:
`role`, `listing_url`, `application_url`, `company`, `location`, `job_description`,
plus optional `platform`, `platform_job_id`, `employer_requisition_id`, `applied_by`
and `notes`. The same optional extracted metadata is also accepted. Unknown keys,
invalid types/URLs, conflicting aliases and inconsistent job identity/destination
are rejected. This prevents record-owned fields such as `status`, paths and
timestamps from being injected by the input.

Mapping is `title → role`, `apply_url → application_url`, and
`canonical_url → canonical_listing_url`. For extractor output, `listing_url` is
also the canonical job URL; `source_url` preserves the observed page URL. This
keeps collection/search pages from becoming capture's job identity. Legacy
`listing_url` values remain unchanged. All optional facts are preserved. Missing
optional facts are never filled with inferred values. Description whitespace is
unchanged by validation. Already normalized data can be validated again.

`ListingError` subclasses `ValueError`, with `field` and `exit_code = 2`. The
capture CLI must catch it before its broader `ValueError` handler and exit using
that code. JSON decoding failures must also be wrapped as `ListingError("input",
"invalid JSON")` before any write. The standalone validator can check a pipe
without capturing anything:

```sh
pbpaste | python3 scripts/listing_input.py
```

In Node, `require("./scripts/extract_linkedin.js")` exports
`{ extractListing, ListingError, SELECTORS }`. `extractListing(document)` returns
the object; the browser IIFE returns its JSON serialization. Browser errors also
have `name = "ListingError"`, `field` and `exit_code = 2`; no JSON is returned on
failure.

## Verification

```sh
node --test tests/extractor.test.cjs
python3 -m unittest discover -s tests -p test_listing_input.py -v
```

The three `tests/fixtures/linkedin-*.html` files are fictional. The Node test
parses their HTML using a minimal standard-library DOM adapter and executes the
actual selectors and browser IIFE. It checks deterministic output, hidden or
absent hiring cards, external URLs, missing fields, stale identity, and absence
of DOM changes. Python tests validate the full schema and exercise exit 2 without
writing files. No live employer or private profile data is needed.
