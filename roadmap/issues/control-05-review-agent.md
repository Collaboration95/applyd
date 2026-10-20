# [P2] Pre-submit reviewer agent compares the filled form with the answer snapshot

## Objective
Pre-submit reviewer agent compares the filled form with the answer snapshot.

## Scope
Before preflight, a second, cheap agent reads the visible filled form values (via accessibility snapshot) and compares them to the frozen answers file and resume. Mismatches (wrong resume file, years field off, wrong radio button selected) block submit with reason review_mismatch and list the differences. Read-only: the reviewer never edits the form.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] A seeded mismatch in a synthetic form snapshot blocks submit with a diff.
- [ ] A matching snapshot passes; the reviewer verdict is stored on the attempt.

## Implementation context
skill apply-linkedin; scripts/workspace.py preflight (accepts reviewer verdict)

## Dependencies
Blocked by: `prepare-answers` (prepare-answers: build each application's answer snapshot from the saved answer bank)
Related existing issues: #20
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P2. Size: Small. Risk: Medium.

## Light verification
Synthetic form snapshots.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:review-agent -->
