# [P1] Dashboard control plane: stat bar, needs-attention, funnel, tabs, filters

## Objective
Dashboard control plane: stat bar, needs-attention, funnel, tabs, filters.

## Scope
Stat bar: applied, awaiting answer, preparing, failed, skipped, today, 7 days. A needs-attention panel lists awaiting_answer, submission_uncertain, failed, and stale preparing/ready jobs, each with reason and direct links. Funnel: captured → ready → submitted → responded. Tabs: Applications (sortable table), Runs (current), Questions, Settings (read-only view of settings.json; edits are downloaded as a change file and applied with a copied CLI command). Filters: company, apply channel/ATS host, date range, applied_by, stop reason, stored in the URL hash. Must still work from file:// with no build step.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] All counts match records and CSV for the synthetic dataset.
- [ ] The needs-attention panel lists every non-terminal, blocked or stale record with its reason.
- [ ] Settings edits produce a downloadable change file plus a copyable command; the UI never says saved before refreshed data confirms it.

## Implementation context
dashboard/*; scripts/export_dashboard.py

## Dependencies
Blocked by: `dashboard-robust` (Dashboard robustness and housekeeping)
Related existing issues: #14, #12, #10
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P1. Size: Medium. Risk: Low.

## Light verification
Extend dashboard tests with the synthetic dataset.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:dashboard-v2 -->
