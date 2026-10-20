# [P2] Read-only Gmail sync: classify replies into outcomes

## Objective
Read-only Gmail sync: classify replies into outcomes.

## Scope
Periodically search Gmail for employer and ATS emails since the last sync and classify each as acknowledged, assessment, interview, rejection, recruiter message or unrelated. Match to an application by company, role and listing ID, and record the outcome event with the message ID as evidence. Ambiguous matches go to needs-attention, not to a guess. Optionally label threads. Never send, reply or delete. Interview or assessment dates create a calendar entry only if the calendar connector is authorised.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Read-only: no send, reply or delete calls are used.
- [ ] Every recorded outcome cites a message ID; ambiguous matches are queued.
- [ ] Re-running the sync does not create duplicate events.

## Implementation context
skill gmail-sync; scripts/workspace.py event

## Dependencies
Blocked by: `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates)
Related existing issues: None
Epic: Step 2 — Feedback: dashboard control plane, outcomes, contacts. Priority: P2. Size: Medium. Risk: Medium.

## Light verification
Synthetic email fixtures for the classifier; manual check on a real inbox.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:gmail-sync -->
