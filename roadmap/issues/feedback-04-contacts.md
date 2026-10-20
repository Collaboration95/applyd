# [P2] Store hiring-team contacts visible on the job page

## Objective
Store hiring-team contacts visible on the job page.

## Scope
contacts[] on the record: name, title/headline, profile_url, email (only if printed in the job description or a confirmation email), source (linkedin_hiring_team | jd_text | confirmation_email), captured_at, relationship (job_poster | hiring_team | unknown), verified=false. Captured only from what the extractor already sees, or from synced emails. No profile visits, no email guessing, no enrichment services. 'workspace.py contact --id ... --add/--remove'. Shown in dashboard details.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] capture --stdin stores the hiring-team card when present, with source and time.
- [ ] Capture rejects contacts with any other source.
- [ ] Contacts appear in dashboard details and in CSV export.

## Implementation context
scripts/workspace.py; scripts/extract_linkedin.js; dashboard

## Dependencies
Blocked by: `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin)
Related existing issues: None
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P2. Size: Small. Risk: Medium.

## Light verification
Synthetic fixtures with and without a hiring-team card.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:contacts -->
