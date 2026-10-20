# [P1] Three lean skills that call scripts instead of restating rules

## Objective
Three lean skills that call scripts instead of restating rules.

## Scope
apply-linkedin: the loop seen.sh → extract → capture → prepare-answers → ready → preflight → submit → event. answer-questions: read the question inbox, save answers in one batch, requeue. cleanup-run: close the run's tabs, reconcile uncertain submissions, export, summary. Each skill is under 60 lines and holds commands and stop rules only. Policy moves out of AGENTS.md prose into settings.json, the answer bank and script checks; AGENTS.md shrinks to pointers.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Each SKILL.md is under 60 lines and contains no duplicated answer values or eligibility prose.
- [ ] The batch prompt files in applications/batches/ no longer need to restate rules; they hold only target and settings overrides.
- [ ] A dry run on synthetic data completes using only the skill instructions.

## Implementation context
.claude/skills/{apply-linkedin,answer-questions,cleanup-run}/SKILL.md; AGENTS.md

## Dependencies
Blocked by: `seen-index` (Ultrafast already-seen check with marker files and seen.sh), `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin), `prepare-answers` (prepare-answers: build each application's answer snapshot from the saved answer bank), `cleanup-run` (End-of-run cleanup: close only the tabs this run opened, reconcile, summarise)
Related existing issues: #7
Epic: Step 1 — Stability: fast, deterministic, scoped execution. Priority: P1. Size: Small. Risk: Low.

## Light verification
Dry run with synthetic fixtures; line-count check.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:skills -->
