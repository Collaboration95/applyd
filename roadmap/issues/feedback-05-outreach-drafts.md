# [P2] Draft-only follow-ups: Gmail drafts and copyable LinkedIn notes

## Objective
Draft-only follow-ups: Gmail drafts and copyable LinkedIn notes.

## Scope
For eligible applications (submitted, no response after N business days, contact present, not on do_not_contact), generate a short message: role, one CV-matching point from projects/catalog.json, thanks. Email: create a Gmail draft only, never send. LinkedIn: a note of at most 200 characters with a copy button and a profile link; the user sends it by hand. At most one follow-up per application, plus a daily draft cap. Drafts and user-confirmed sends are logged on the record. No automated LinkedIn messaging or invites.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] No code path calls a send API; only draft creation is used.
- [ ] Caps, the do_not_contact list and the one-follow-up limit are enforced from records.
- [ ] The draft text and its sources are stored on the record.

## Implementation context
skill outreach; scripts/workspace.py; dashboard (copy button)

## Dependencies
Blocked by: `contacts` (Store hiring-team contacts visible on the job page), `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates), `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
Related existing issues: None
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P2. Size: Small. Risk: High.

## Light verification
Synthetic eligibility cases; draft-only check.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:outreach-drafts -->
