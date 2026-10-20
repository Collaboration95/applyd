# [P1] Outcome tracking: interview, assessment, rejection, offer, follow-up dates

## Objective
Outcome tracking: interview, assessment, rejection, offer, follow-up dates.

## Scope
Add the statuses acknowledged, assessment, interview, rejected, offer and withdrawn as follow-up events on a submitted record. They never change the original submission time or run grouping. Optional follow_up_at. 'workspace.py event --status interview --at <time> --note ... --evidence ...'. The exporter passes outcome fields through. The dashboard shows a Response column and an overdue follow-up list.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Outcome events keep the submission in its original run.
- [ ] The exporter includes outcome, latest outcome time and follow_up_at.
- [ ] Overdue follow-ups (default 14 days, set in settings) appear in needs-attention.

## Implementation context
scripts/workspace.py event; scripts/export_dashboard.py; dashboard

## Dependencies
Blocked by: None
Related existing issues: #22
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P1. Size: Small. Risk: Low.

## Light verification
Synthetic event sequences.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:outcomes -->
