# [P1] Synthetic demo dataset and automated screenshots

## Objective
Synthetic demo dataset and automated screenshots.

## Scope
demo/: about 15 fictional applications across statuses, runs, outcomes and stop reasons, with fictional companies and no real data. 'scripts/screenshots.sh' opens the dashboard with demo data in a headless browser and writes docs/img/*.png plus a short GIF of a run. The privacy check (#4) scans them.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Demo data passes the privacy canary check.
- [ ] Screenshots regenerate from one command and are the same each time apart from the timestamp.

## Implementation context
demo/; scripts/screenshots.sh; docs/img/

## Dependencies
Blocked by: #4
Related existing issues: #23
Epic: Step 2 — Public repo presence. Priority: P1. Size: Small. Risk: Medium.

## Light verification
Privacy check plus a visual review.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:demo-data -->
