# [P1] Public README, setup guide, architecture diagram, repo description

## Objective
Public README, setup guide, architecture diagram, repo description.

## Scope
README: banner/mascot, a one-line description, a screenshot/GIF, and a 3-step quickstart (init → edit settings.json → run skill / open dashboard). Also an architecture diagram (seen → extract → capture → prepare-answers → preflight → submit → cleanup → sync), a safety section (user-approved outreach, no scraping, confirmation-required submission, private data never committed), and a FAQ. GitHub description and topics. Write no personal facts anywhere.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] A new user can reach the demo dashboard by following only the README on a clean clone.
- [ ] The README contains no private data or filenames (privacy check passes).

## Implementation context
README.md (public); docs/

## Dependencies
Blocked by: `demo-data` (Synthetic demo dataset and automated screenshots), #4
Related existing issues: #9, #23
Epic: Step 2 — Public repo presence. Priority: P1. Size: Small. Risk: Medium.

## Light verification
Fresh-clone walkthrough.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:readme -->
