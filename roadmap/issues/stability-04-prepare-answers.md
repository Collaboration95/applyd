# [P1] prepare-answers: build each application's answer snapshot from the saved answer bank

## Objective
prepare-answers: build each application's answer snapshot from the saved answer bank.

## Scope
'workspace.py prepare-answers --id <app> --questions <questions.json>' takes the exact form questions the agent saw. It maps each one to a saved answer, an authorised default or a routine employer-preferred option, records the basis for each, and writes the answers file used by 'event --status ready'. Unmatched questions are listed with stable question IDs, and the application goes to awaiting_answer with structured blocker IDs instead of prose. The answer bank is loaded once per call. No model calls.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Known questions and synonyms resolve to saved values, with source/scope/basis recorded for each answer.
- [ ] Null or unknown answers never reach 'ready'; unmatched questions produce blocker IDs.
- [ ] Output is byte-identical for identical inputs.

## Implementation context
scripts/workspace.py; profile/answers.json (aliases)

## Dependencies
Blocked by: None
Related existing issues: #8, #11
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P1. Size: Small. Risk: Medium.

## Light verification
Synthetic question sets: all known, one unknown, and a synonym.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:prepare-answers -->
