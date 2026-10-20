# [P0] Account-safety stop: captcha, warnings, login walls, restriction notices

## Objective
Account-safety stop: captcha, warnings, login walls, restriction notices.

## Scope
Define detectors for the extractor and skill: captcha or challenge page, 'unusual activity' or restriction notice, unexpected sign-in page, rate-limit message. Any match stops the whole run (not only the job) with a typed run stop reason and a screenshot, then sends a push notification. Also add a manual kill switch: if the file applications/STOP exists, the next command refuses to start new work.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] A detector match ends the run with stop_reason=account_safety and no further submissions.
- [ ] Creating applications/STOP makes seen/capture/preflight refuse new work, and the run records it.
- [ ] The user receives one notification per stop.

## Implementation context
scripts/extract_linkedin.js; scripts/workspace.py; skill apply-linkedin

## Dependencies
Blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
Related existing issues: #17
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P0. Size: Small. Risk: High.

## Light verification
Synthetic challenge-page fixtures; STOP file test.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:kill-switch -->
