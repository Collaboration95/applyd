# [P2] Weekly report: volume, response rates by preset/role/channel, effort

## Objective
Weekly report: volume, response rates by preset/role/channel, effort.

## Scope
'workspace.py report --week' prints a one-screen summary computed from records: applications, responses, and response rate by resume preset, role tag and channel (Easy Apply vs external). Also median time per confirmed application, top stop reasons, and pending questions. Deterministic, no model calls.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Same records produce the same report.
- [ ] Every figure comes from record fields; no estimates.

## Implementation context
scripts/workspace.py report

## Dependencies
Blocked by: `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates)
Related existing issues: #22, #15
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P2. Size: Small. Risk: Low.

## Light verification
Synthetic dataset snapshot test.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:weekly-report -->
