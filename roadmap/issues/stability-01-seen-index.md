# [P0] Ultrafast already-seen check with marker files and seen.sh

## Objective
Ultrafast already-seen check with marker files and seen.sh.

## Scope
Use the filesystem as the hash set. Keep one marker file per identity key under applications/index/. Key names: li-<linkedin job id>; url-<sha1 of canonical URL> for non-LinkedIn listings; req-<sha1 of company|requisition id>; xpost-<sha1 of normalised company|title|location>. Each marker holds one line: '<application_id> <status>'. workspace.py writes markers atomically on capture and on every status event. Add 'workspace.py reindex' to rebuild all markers from records, removing stale ones. Add scripts/seen.sh (POSIX sh, no Python): 'seen.sh <id>...' prints '<id> new' or '<id> <status> <application_id>' per line. It accepts many IDs in one call so a whole search-results page is checked at once. The agent runs seen.sh before opening any job card and opens only 'new' IDs, or IDs whose status is in a retryable set taken from settings.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] seen.sh checks 25 IDs in one call in under 20 ms on a laptop, with no Python process started.
- [ ] capture and event keep markers consistent with records; reindex from records gives byte-identical markers.
- [ ] A LinkedIn ID, canonical URL, requisition ID or cross-post identity that is already stored is reported as seen; an unknown ID is reported as new.
- [ ] capture's own duplicate check uses the index instead of scanning every record.json.

## Implementation context
scripts/seen.sh (new); scripts/workspace.py capture/event/reindex; applications/index/

## Dependencies
Blocked by: None
Related existing issues: #3, #16
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P0. Size: Small. Risk: Medium.

## Light verification
Synthetic records: reindex round trip, stale-marker removal, a timing check on 25 IDs, and the cross-post key.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:seen-index -->
