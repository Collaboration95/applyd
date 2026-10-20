# [P1] End-of-run cleanup: close only the tabs this run opened, reconcile, summarise

## Objective
End-of-run cleanup: close only the tabs this run opened, reconcile, summarise.

## Scope
Record every tab the run opens in applications/runs/<run-id>/tabs.json (window/tab id + URL at open). scripts/cleanup_tabs.sh uses osascript against Brave (Chromium AppleScript dictionary) and closes only tabs in that list. It skips any tab whose current host is no longer an allowed host, and any form tab whose application is submission_uncertain. It never closes the user's own tabs. Afterwards: reconcile submission_uncertain entries, run export, and write a run summary (applied / skipped by reason / awaiting answers / failures, with links).

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Tabs not listed in tabs.json are never closed; a test with a fake tab list proves the filtering.
- [ ] Uncertain-submission tabs are left open and listed in the summary.
- [ ] The summary JSON and its Markdown are written for every run, including stopped runs.

## Implementation context
scripts/cleanup_tabs.sh (new); scripts/workspace.py run-summary; applications/runs/

## Dependencies
Blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
Related existing issues: #16
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P1. Size: Small. Risk: Medium.

## Light verification
Filter logic tested with synthetic tab lists; the osascript call stubbed in tests.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:cleanup-run -->
