# [P0] Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists

## Objective
Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists.

## Scope
Private settings.json with a schema version, loaded by every command. Keys: mode (apply | save_only | dry_run); easy_apply_only (bool); external_hosts allow/deny; daily_cap; per_run_cap; per_run_minutes; company_blocklist; title_blocklist (regex); max_required_years; retryable_statuses; start_mode (e.g. begin from LinkedIn saved jobs vs recommended vs a search URL). save_only captures listings and optionally clicks LinkedIn Save, without applying. dry_run fills forms but never clicks Submit and records 'dry_run_complete'. Each run stores a snapshot of the settings it used. Daily counters come from records, not memory.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Preflight refuses to submit in save_only or dry_run mode, or when the daily cap is reached (computed from records).
- [ ] easy_apply_only=true makes external-apply jobs end as skipped:external_apply without being opened.
- [ ] Blocklisted company or title ends as skipped:blocklist before extraction finishes.
- [ ] The run record stores the exact settings snapshot and its hash.

## Implementation context
settings.json (private) + settings.example.json; scripts/workspace.py

## Dependencies
Blocked by: None
Related existing issues: #5, #18, #21
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P0. Size: Small. Risk: Medium.

## Light verification
Synthetic preflight cases for each mode, the cap and the blocklist.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:settings -->
