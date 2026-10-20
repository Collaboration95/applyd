# [P2] Scheduled unattended runs with an end-of-run notification

## Objective
Scheduled unattended runs with an end-of-run notification.

## Scope
Scheduled runs (e.g. weekdays 09:00: 10 Easy Apply jobs within settings). Each run is time- and count-bounded. It ends with cleanup and a push notification linking the run summary and the number of questions waiting. Runs are skipped if a STOP file or an unresolved account_safety stop exists.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] A scheduled run respects caps and time budget, and ends with cleanup + notification.
- [ ] No run starts while a STOP or account_safety stop is unresolved.

## Implementation context
scheduler config; skill apply-linkedin

## Dependencies
Blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists), `kill-switch` (Account-safety stop: captcha, warnings, login walls, restriction notices), `cleanup-run` (End-of-run cleanup: close only the tabs this run opened, reconcile, summarise), `skills` (Three lean skills that call scripts instead of restating rules)
Related existing issues: #19, #21
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P2. Size: Small. Risk: Medium.

## Light verification
Dry-run mode schedule on synthetic data.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:schedule -->
