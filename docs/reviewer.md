# Pre-submit snapshot reviewer

`scripts/reviewer.py` implements the deterministic comparison layer for issue
#15. It is standard-library-only and read-only: it does not open the browser,
read workspace files, edit a form, or call a model. A browser/agent adapter takes
one accessibility-derived snapshot of the filled form and the currently attached
resume, then passes that snapshot to `review`.

## Input contract

The adapter calls:

```python
from reviewer import review, validate_verdict

verdict = review(prepared, frozen_answers, visible)
# Store verdict privately on this exact attempt before the parent's preflight.
check = validate_verdict(verdict, prepared, frozen_answers, visible)
if not check["valid"] or not check["passed"]:
    # Stop this attempt. A failed comparison has stop_reason=review_mismatch.
    ...
```

`prepared` is the immutable attempt metadata and has this reviewer-facing shape:

```json
{
  "attempt_id": "attempt-unique-id",
  "answers_sha256": "<SHA-256 of the exact frozen answers.json bytes>",
  "sha256": "<SHA-256 of the exact frozen resume copy bytes>"
}
```

The workspace ready path already stores `answers_sha256` and `sha256` on the
prepared record. The parent glue must assign or supply a unique `attempt_id` for
each ready attempt when it invokes this API. The resume hash is the digest of the
actual attached file bytes reported by the adapter; a filename or visible label
alone is not evidence of the file contents.

`frozen_answers` is the exact list persisted in the frozen answers file. Each
field uses the answer-core row schema; the reviewer reads `occurrence_id` (or an
adapter-preserved `field_id`/`id`), exact `question`, `type`, and `answer`.
`visible` is one captured adapter snapshot:

```json
{
  "attempt_id": "attempt-unique-id",
  "resume_sha256": "<SHA-256 of attached resume bytes>",
  "fields": [
    {
      "id": "python-experience-occurrence-1",
      "question": "Years of Python experience?",
      "type": "number",
      "value": 1
    }
  ]
}
```

The field ID must map to the frozen occurrence ID. The adapter should extract
values from the visible/accessibility representation, preserving the value type
(for example, number `1` versus text `"1"`, boolean `true` versus number `1`).
Comparisons of IDs, question text, field type, selected values, attempt identity,
and hashes are exact. The reviewer does not normalize wording or infer values.

## Verdict and persistence

`review` returns `schema_version`, `passed`, `stop_reason`, `differences`, and a
`binding` with the frozen answer hash, resume hash, and attempt ID. A match returns
`passed: true` and `stop_reason: null`. Any discrepancy returns
`passed: false`, `stop_reason: "review_mismatch"`, and a stable, sorted list of
differences. Missing, duplicate, or unknown fields are discrepancies. Differences
include expected/actual values when a selected value differs, including radio and
numeric values, so the verdict is private application data. Store it only in the
private attempt record; do not export it to the dashboard, repository, or public
logs.

`validate_verdict(verdict, prepared, frozen_answers, visible)` recomputes the
verdict and requires exact equality with the stored verdict. It returns
`{"valid": false, ...}` if the attempt, frozen answers, attached resume, or visible
snapshot changed after review. A matching stored failure is valid evidence of a
block, but callers must require both `valid` and `passed` before allowing
preflight to continue. The parent's preflight glue remains responsible for
persisting the verdict and enforcing this check before any submit authorization.

This contract and its tests use synthetic data only. They verify the deterministic
comparison API; they do not claim that a real agent, browser adapter, accessibility
snapshot, or employer form has been exercised.

The CLI `workspace.py review-form --id <app> --visible <snapshot.json>` saves a new immutable review snapshot with the attempt. Preflight requires a matching passing verdict, including attachment and answer hashes. Failed differences remain private; the CLI returns only difference codes and a private snapshot reference. The reviewer must independently observe the actual filled form/attachment rather than copy expected values from the answer file.
