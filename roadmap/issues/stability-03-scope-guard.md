# [P1] Stay on the LinkedIn page: per-run domain allowlist and off_scope stop

## Objective
Stay on the LinkedIn page: per-run domain allowlist and off_scope stop.

## Scope
Each run declares its allowed hosts: linkedin.com, plus employer ATS hosts only if settings allow external applications. Job facts come only from the extractor on the LinkedIn job page. The agent does not search the web, open company sites, or browse Glassdoor or profiles. Navigation outside the allowlist ends that job with stop reason off_scope, records the URL, and moves to the next job. Write a short rule into the apply skill and enforce it in the runner/preflight. Preflight fails if the record's apply URL host is not allowed.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Preflight rejects an application whose destination host is not allowed by the run settings.
- [ ] An off_scope stop is recorded as a typed reason and shown on the dashboard.
- [ ] Captured facts have source = linkedin_job_page; other sources are rejected by capture.

## Implementation context
scripts/workspace.py preflight/capture; skill apply-linkedin

## Dependencies
Blocked by: `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin), `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
Related existing issues: #17, #18
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P1. Size: Small. Risk: Medium.

## Light verification
Synthetic records with allowed and disallowed hosts.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:scope-guard -->
