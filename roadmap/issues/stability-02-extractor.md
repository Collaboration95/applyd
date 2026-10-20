# [P0] Deterministic LinkedIn job-page extractor + capture --stdin

## Objective
Deterministic LinkedIn job-page extractor + capture --stdin.

## Scope
A single JavaScript snippet (scripts/extract_linkedin.js) run in the open LinkedIn job page. It returns JSON with: platform_job_id, title, company, location, workplace type, employment type, full job description text, easy_apply (bool), apply_url (external, if any), posted-age text, and the hiring-team/job-poster card (name, headline, profile URL) when visible. It reads only the current DOM: no navigation, no fetches, no profile visits. 'workspace.py capture --stdin' accepts that JSON directly and validates it against a schema. Selectors live in one place with fallbacks; any missing required field is a typed error, never a guess. Retire the hand-written tmp/job-input-*.json flow.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] The same saved synthetic HTML fixture always gives identical JSON.
- [ ] A missing required field gives exit code 2 and a field name, never partial capture.
- [ ] The agent workflow is: seen.sh → open new job → run snippet → pipe to capture --stdin. No hand-written JSON.
- [ ] The snippet makes no network requests and does not change the page.

## Implementation context
scripts/extract_linkedin.js (new); scripts/workspace.py capture; tests/fixtures (synthetic HTML)

## Dependencies
Blocked by: `seen-index` (Ultrafast already-seen check with marker files and seen.sh)
Related existing issues: #7
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P0. Size: Medium. Risk: Medium.

## Light verification
Run against 2-3 synthetic HTML fixtures, including one with no hiring-team card and one external-apply job.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:extractor -->
