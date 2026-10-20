# [P1] Dashboard robustness and housekeeping

## Objective
Dashboard robustness and housekeeping.

## Scope
Skip invalid records and show a count banner instead of blanking the page (app.js:31). Number runs by start time so the numbers stay stable. Show only status filter options that exist in the data. The status filter matches current status only. Imports keep top-level keys. Remove dead CSS, add a narrow-screen media query, and use a neutral <title>.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] A malformed record shows a banner; every other record still renders.
- [ ] Run numbers do not change when an older JSON file is imported.
- [ ] No personal name appears in index.html.

## Implementation context
dashboard/app.js, runs.js, style.css, index.html

## Dependencies
Blocked by: None
Related existing issues: #4, #14
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P1. Size: Small. Risk: Low.

## Light verification
Extend tests/test_dashboard.py and run-grouping.test.cjs with a malformed synthetic record.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:dashboard-robust -->
