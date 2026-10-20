# [Epic] Step 1 — Stability: fast, deterministic, scoped execution

## Outcome
The agent spends its time only on the parts of an application that need judgement. Duplicate checks, page extraction, answer assembly and staying in scope are handled by scripts, quickly and the same way every time.

## Sub-issues
- [ ] `seen-index` [P0] Ultrafast already-seen check with marker files and seen.sh
- [ ] `extractor` [P0] Deterministic LinkedIn job-page extractor + capture --stdin
- [ ] `scope-guard` [P1] Stay on the LinkedIn page: per-run domain allowlist and off_scope stop
- [ ] `prepare-answers` [P1] prepare-answers: build each application's answer snapshot from the saved answer bank
- [ ] `dashboard-robust` [P1] Dashboard robustness and housekeeping
- [ ] `skills` [P1] Three lean skills that call scripts instead of restating rules

## Completion
All sub-issue acceptance checks pass. No real employer submission or private data is needed for verification.

<!-- agent-orchestration:issue-key=auto-linkedin:epic-stability -->
