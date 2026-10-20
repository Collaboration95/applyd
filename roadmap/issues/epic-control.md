# [Epic] Step 2 — Run control: settings, safety stops, cleanup, unattended runs

## Outcome
Runs are configured by a settings file instead of prompt prose, stop safely on their own, clean up after themselves and report back. The user does not have to watch them.

## Sub-issues
- [ ] `settings` [P0] Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists
- [ ] `kill-switch` [P0] Account-safety stop: captcha, warnings, login walls, restriction notices
- [ ] `cleanup-run` [P1] End-of-run cleanup: close only the tabs this run opened, reconcile, summarise
- [ ] `schedule` [P2] Scheduled unattended runs with an end-of-run notification
- [ ] `review-agent` [P2] Pre-submit reviewer agent compares the filled form with the answer snapshot
- [ ] `computer-use` [P2] Computer-use fallback for ATS widgets and file-upload dialogs only

## Completion
All sub-issue acceptance checks pass. No real employer submission or private data is needed for verification.

<!-- agent-orchestration:issue-key=auto-linkedin:epic-control -->
