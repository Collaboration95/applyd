# Master plan: applyd

## Outcome

Deliver a small local-first application assistant that runs bounded unattended sessions, collects unknown questions in one place, reuses scoped answers, resumes interrupted work, and preserves confirmed submissions without duplicates. Finish with runnable code, concise setup instructions, and light synthetic verification. This document is the implementation handoff; backlog initialization itself does not implement the features.

Repository: https://github.com/Collaboration95/applyd (project formerly named auto-linkedin)

Active roadmap (v2): https://github.com/users/Collaboration95/projects/17, generated from `roadmap/backlog-v2.json`.

Original backlog referenced below (issues #1–#23): https://github.com/Collaboration95/auto-linkedin, project https://github.com/users/Collaboration95/projects/15. In applyd issues these appear as `Collaboration95/auto-linkedin#N`.

## Highest-priority invariant: private data never enters GitHub

The remote repository is public. No real application records, profile values, resume contents, personal resume filenames, private source paths, contact details, employer-specific answers, screenshots, browser/session credentials or generated private exports may enter Git history, issues, PRs, Actions artifacts, demos, screenshots, or shared logs. Filename/path disclosure counts as a leak even when file bytes are absent. Use synthetic examples only.

Bootstrap `.gitignore` denies every path except itself and this plan. No files were staged or committed during initialization. Existing code, local instructions, personal documents and generated exports remain ignored until the privacy issue passes. Never use force-add or relax the allowlist just to make a commit succeed. Preserve existing private files and original bytes; no destructive migration. A deny-by-default ignore file is initial containment, not completion of the full privacy issue.

Implement a private workspace outside the distributable tree, with an explicit workspace selector and a non-destructive compatibility path. Audit and allowlist generic code, tests, configuration examples and documentation one path at a time. Do not publish the existing personal AGENTS.md or README.md unchanged. Keep private workflow guidance in the private workspace and provide neutral contributor instructions for the public repository.

## Fixed decisions: proceed without routine human input

- Keep Python helpers, JSON records and plain HTML/CSS/JavaScript. No database, hosted backend, account system, framework migration or vector store.
- Support macOS first; make workspace paths portable and document Linux/Windows limits. Do not claim cross-platform browser support without evidence.
- Keep the dashboard server-free for v0.1. Batch answer edits export one JSON change file; one CLI import validates and persists it. A browser download is not a completed save.
- Use one browser writer per session and a deterministic state/queue controller. Use an agent for ambiguous interpretation and unfamiliar form handling; do not build an independent multi-agent service.
- Store canonical question IDs separately from per-application occurrence IDs. Exact normalized aliases plus field type, options, scope and units determine reuse; ambiguous matches stay unresolved.
- Preserve explicit user facts and authorized defaults with provenance. Unknown material facts remain null and are queued; unattended mode does not invent declarations. Scope and expiry apply to every reused answer.
- Distinguish legally authorized to work, sponsorship, residency, willingness and employer-specific consent. Defaults cannot collapse these concepts.
- Use existing selected resume policy; no automatic tailoring project. Preserve exact submitted bytes privately.
- Use MIT for project-owned code as the release default; preserve existing third-party notices.
- Use authenticated `gh` CLI exclusively for GitHub access. Local Git commands are allowed for initialization, status, staging and local history.
- Use synthetic jobs, profiles, names and resume filenames in tests, demos, documentation and verification evidence. No live employer submission is required or authorized by this implementation plan.
- Do not install schedules, cloud infrastructure or paid services as part of implementation. Existing browser/auth limitations become typed blockers; do independent work without repeated permission questions.

## Backlog model

Twenty implementation issues are grouped under three aggregate epics. Epics are organizational containers, not additional coding work. Priority indicates impact; Sequence is the deterministic execution order and includes necessary prerequisites. P0 is privacy and submission/answer correctness, P1 is the working unattended/review path, and P2 is bounded efficiency work.

Workflow is authoritative on the Project: Backlog, Ready, In progress, Review, Blocked, Done. Built-in Status is coarse. Only the privacy issue starts Ready; others remain Blocked until their native dependencies pass acceptance. Use the single finite v0.1 milestone rather than invented calendar sprints. No recurring iteration is required. Suggested board views: roadmap grouped by Parent issue; Ready sorted by Sequence; Review; Blocked. Saved custom views are not required for execution.

- [[Epic] Private workspace and distributable foundation](https://github.com/Collaboration95/auto-linkedin/issues/1)
- [[Epic] Answer once and review in one place](https://github.com/Collaboration95/auto-linkedin/issues/2)
- [[Epic] Bounded deterministic application execution](https://github.com/Collaboration95/auto-linkedin/issues/3)

## Dependency-ordered execution

Execute this sequence serially by default. Parallel read-only reviews are fine; parallel code writers are unnecessary for this small project. Do not skip a failed prerequisite.

### 01. P0 — Separate public code from all private data

Issue: [#4](https://github.com/Collaboration95/auto-linkedin/issues/4) · Dependencies: None · Size: Small · Risk: High · Original shortlist: 17

Keep existing private files and their bytes intact. Introduce an external/private workspace boundary, an audited public allowlist, synthetic examples, generic documentation and a neutral page title. Remove identifying values AND personal artifact filenames from public code/docs/fixtures, exports, screenshots and logs. Do not copy private filenames into issue/PR/test output.

Acceptance:

- A clean Git index/release manifest contains only audited generic files; no real profile, application, resume or source artifacts, including their filenames.
- Normal git add cannot stage private workspace contents; a deterministic publication check fails on seeded synthetic leak markers.
- Existing private workspace still resolves its files without destructive migration; original bytes remain unchanged.

Light verification: Use synthetic privacy canaries and git check-ignore; inspect staged paths and content without printing private matches. No live submission.

### 02. P1 — Portable workspace configuration

Issue: [#5](https://github.com/Collaboration95/auto-linkedin/issues/5) · Dependencies: #4 · Size: Small · Risk: Medium · Original shortlist: 18

Introduce --workspace/environment configuration with explicit precedence and defaults. Move personal role rules, timezone, resume selection and browser choice into private configuration. Generic AGENTS.md must never embed one person's facts.

Acceptance:

- Commands run against two independent synthetic workspaces without cross-reading data.
- Artifacts persist relative to the selected workspace; copying a synthetic workspace preserves resolution.
- Timezone-aware timestamps use configured timezone; missing config yields actionable messages.

Light verification: Use two temporary workspace fixtures and one relocated fixture.

### 03. P0 — Persist submission intent and prevent duplicate submission

Issue: [#6](https://github.com/Collaboration95/auto-linkedin/issues/6) · Dependencies: #4, #5 · Size: Medium · Risk: High · Original shortlist: 5

Add atomic begin-submit with attempt identity and browser-observed job identity; enforce one browser writer/claim. Check previous confirmations before clicking. Crash recovery must reconcile unfinished intent rather than blindly retry.

Acceptance:

- Mismatch between observed job and prepared record blocks submit.
- A confirmed job cannot be submitted again through the runner even after stale-page restore.
- A crash between intent, click and confirmation becomes uncertain and cannot retry without reconciliation.
- Concurrent claim attempt fails safely; one winner only.

Light verification: Synthetic stale-page, duplicate, crash-window and competing-claim tests; no live application.

### 04. P1 — Versioned compact run context

Issue: [#7](https://github.com/Collaboration95/auto-linkedin/issues/7) · Dependencies: #5 · Size: Small · Risk: Medium · Original shortlist: 10

Produce one minimal answer/preference/resume/blocker snapshot and content version per run; refresh on saved updates. Keep submit-time freshness validation.

Acceptance:

- Identical inputs yield the same context version.
- An answer update invalidates the version; unrelated logging does not force full profile rereads.
- Context stays private and is omitted from public telemetry.

Light verification: Test stable hashes, one changed answer and private output destination.

### 05. P0 — Validate answer provenance and freshness

Issue: [#8](https://github.com/Collaboration95/auto-linkedin/issues/8) · Dependencies: #7, #6 · Size: Medium · Risk: High · Original shortlist: 11

Centralize answer resolution/validation. Preserve source ID/version, exact selected value and explicit-default basis. Revalidate scope, expiry, allowed options and units before submit.

Acceptance:

- Expired or changed answers after preparation fail preflight.
- Employer-specific consent cannot be reused globally; sponsorship and authorization stay distinct.
- Null/invalid required values block only the affected application; exact snapshots remain immutable.

Light verification: Focused expiry-after-ready, scope mismatch and authorized-default fixtures.

### 06. P1 — Initialize a workspace and diagnose prerequisites

Issue: [#9](https://github.com/Collaboration95/auto-linkedin/issues/9) · Dependencies: #5 · Size: Small · Risk: Medium · Original shortlist: 19

Add init and doctor commands, synthetic defaults, a documented supported Python/Node baseline and optional PDF-tool checks. Support macOS first and document other OS limits; no broad packaging rewrite.

Acceptance:

- Fresh init creates valid empty private data without model/network calls.
- Doctor distinguishes missing optional PDF support from blockers, avoids personal paths in shared reports, and never modifies an authenticated browser.
- Document one setup and one verification command.

Light verification: Run init/doctor in an empty temporary directory and simulate one missing dependency.

### 07. P1 — Aggregate questions into an inbox with stable IDs

Issue: [#10](https://github.com/Collaboration95/auto-linkedin/issues/10) · Dependencies: #5 · Size: Small · Risk: Medium · Original shortlist: 1

Introduce canonical question IDs and separate per-form occurrence IDs. Export one Questions tab with exact wording/options, unresolved status, affected jobs, and saved-record/original-listing links.

Acceptance:

- IDs survive reorder/export/restart; equivalent occurrences link to one canonical item when explicitly mapped.
- Unanswered questions appear without scrolling through run history.
- Each occurrence retains company/application context and exact wording without exposing private data in release assets.

Light verification: Synthetic repeated and distinct questions; desktop/mobile tab and deep-link smoke.

### 08. P1 — Answer known aliases deterministically within scope

Issue: [#11](https://github.com/Collaboration95/auto-linkedin/issues/11) · Dependencies: #10, #8 · Size: Medium · Risk: Medium · Original shortlist: 2

Resolve approved aliases through exact normalized matching, field type, units and scope. Unknown or ambiguous wording enters the inbox; no embeddings/vector database.

Acceptance:

- Known aliases reuse one answer without model calls.
- Different countries, currency/period, legal meanings or consent scopes do not merge.
- An unfamiliar question is queued, not guessed; saved explicit answers beat defaults.

Light verification: Table-driven alias, unit and negative scope cases.

### 09. P1 — Save reviewed answers from the Questions tab

Issue: [#12](https://github.com/Collaboration95/auto-linkedin/issues/12) · Dependencies: #11 · Size: Small · Risk: Medium · Original shortlist: 3

Keep v1 server-free: export a schema-versioned answer-change file, import with a single CLI command under lock, then refresh. Show pending-export separately from persisted state. One batch import, no per-answer chat interaction.

Acceptance:

- Batch edit/export/import updates answers once and records scope/source/date.
- Invalid or stale-base changes fail without partial writes; duplicate import is harmless.
- UI does not claim saved until authoritative refreshed data confirms persistence.

Light verification: One round trip plus stale revision and malformed payload tests; no personal fixture.

### 10. P1 — Requeue jobs when all answer blockers resolve

Issue: [#13](https://github.com/Collaboration95/auto-linkedin/issues/13) · Dependencies: #12 · Size: Small · Risk: Medium · Original shortlist: 4

Persist blocked_on_answer_ids; recompute runnable jobs after answer changes. Prioritize resolved work without changing submitted or uncertain jobs.

Acceptance:

- Resolving a shared answer requeues all otherwise eligible affected jobs.
- A job with a second unresolved blocker remains waiting.
- Resume freshness and current listing eligibility are rechecked before execution.

Light verification: Two jobs sharing a blocker, one extra blocker and a submitted record.

### 11. P1 — Attention-first dashboard with direct links

Issue: [#14](https://github.com/Collaboration95/auto-linkedin/issues/14) · Dependencies: #13 · Size: Small · Risk: Medium · Original shortlist: 6

Show unresolved questions, uncertain submissions and outdated facts at the top; keep run log available below. Add application anchors and unlock counts derived from queue state.

Acceptance:

- No unanswered item requires opening long descriptions to find it.
- Unlock counts match all-blockers-resolved semantics, not mere references.
- Keyboard navigation and a narrow-screen smoke pass; history grouping remains unchanged.

Light verification: Synthetic dashboard smoke at desktop/narrow width and existing grouping tests.

### 12. P1 — Derive batch quotas and actual event times from records

Issue: [#15](https://github.com/Collaboration95/auto-linkedin/issues/15) · Dependencies: #6, #13 · Size: Small · Risk: Medium · Original shortlist: 8

Store batch_id and category on attempts, derive unique confirmed totals, separate submitted exclusions from retryable blockers. Persist occurred_at separately from recorded_at for delayed confirmations.

Acceptance:

- Prior submissions and duplicate attempts never increment a new batch quota.
- Queued jobs become eligible after resolution rather than remaining excluded.
- Delayed recording preserves original submission-time grouping; timestamps require offsets.

Light verification: Quota, queued-retry and delayed-cross-boundary tests.

### 13. P1 — Recover export failures and avoid repeated record scans

Issue: [#16](https://github.com/Collaboration95/auto-linkedin/issues/16) · Dependencies: #5 · Size: Small · Risk: Medium · Original shortlist: 14

Separate authoritative mutation success from export status. Idempotent retries repair exports without repeating actions. Read one record snapshot per export; add optional deferred batch export.

Acceptance:

- Injected export failure preserves successful record mutation and reports both outcomes.
- Retry repairs dashboard without adding a duplicate event/submission.
- Default auto-export still works; deferred export produces equivalent final data.

Light verification: One export-failure injection and equivalence test.

### 14. P1 — Typed stop reasons and bounded retries

Issue: [#17](https://github.com/Collaboration95/auto-linkedin/issues/17) · Dependencies: #6, #15, #16 · Size: Small · Risk: Medium · Original shortlist: 9

Add machine-readable blockers for unknown answer, login, page change, throttling and uncertain submit. Persist retry counts and next eligible time; continue independent work.

Acceptance:

- Repeated identical failure exhausts a finite retry budget.
- Login/page-change stops browser actions and records a resumable reason.
- Uncertain submissions never enter automatic submit retry; stop is durable.

Light verification: Simulated repeated timeout/login/uncertain scenarios.

### 15. P2 — Deterministic eligibility rules with reason codes

Issue: [#18](https://github.com/Collaboration95/auto-linkedin/issues/18) · Dependencies: #7, #15 · Size: Small · Risk: Medium · Original shortlist: 12

Apply explicit location/experience/sponsorship exclusions before expensive form work. Distinguish hard requirements from preferences; return needs_review for ambiguity.

Acceptance:

- Explicit exclusions produce stable reason codes without a model.
- Boundary wording such as 3+ versus a hard minimum of 4 follows configured rules.
- Ambiguous listings do not become fabricated eligibility decisions.

Light verification: Small fixture table for include/exclude/ambiguous cases.

### 16. P1 — Resumable deterministic next-action runner

Issue: [#19](https://github.com/Collaboration95/auto-linkedin/issues/19) · Dependencies: #17, #13, #18 · Size: Medium · Risk: Medium · Original shortlist: 7

Return one next action from durable state and execute bounded steps through an adapter. Use existing agent/browser tools for judgment and interaction. No new orchestration service; single browser writer.

Acceptance:

- Restart resumes from the last verified checkpoint and never replays a confirmed submit.
- Resolved jobs precede fresh discovery; remaining quota is respected.
- Agent receives only minimal action context; all durable decisions survive process exit.

Light verification: Synthetic interrupted run with one blocked job, one confirmed job and successful restart.

### 17. P2 — Reusable handlers for common application forms

Issue: [#20](https://github.com/Collaboration95/auto-linkedin/issues/20) · Dependencies: #19, #11 · Size: Medium · Risk: Medium · Original shortlist: 13

Implement only common observed Easy Apply field types via semantic labels/options and an adapter contract. Detect changed layouts; verify selected resume/current application history. Delegate unfamiliar forms to the agent.

Acceptance:

- Known forms fill approved answers and verify selected values deterministically.
- Unknown layout/options stop or fall back rather than blind clicking.
- Resume selection and work-history values match saved facts; no unsolicited profile edits.

Light verification: Local synthetic form smoke for text/select/upload and one changed-layout fallback; never submit to an employer.

### 18. P1 — Bound unattended runs by time and work budgets

Issue: [#21](https://github.com/Collaboration95/auto-linkedin/issues/21) · Dependencies: #19 · Size: Small · Risk: Medium · Original shortlist: 15

Add configured deadline, quota, retries and optional measured provider budget. Check at action boundaries, checkpoint and emit short handoff. Do not install recurring schedules automatically.

Acceptance:

- Deadline/quota/retry limits stop new work and preserve restart state.
- Unknown token costs remain unavailable rather than invented; time/quota limits still enforce bounds.
- A submit already begun follows uncertainty/confirmation protocol on interruption.

Light verification: Fake-clock deadline and quota boundary tests.

### 19. P2 — Measure effort per unique confirmed application

Issue: [#22](https://github.com/Collaboration95/auto-linkedin/issues/22) · Dependencies: #21, #20 · Size: Small · Risk: Medium · Original shortlist: 16

Record stage durations, retries, answer reuse and optional provider usage; aggregate locally. Keep content, identifiers and personal filenames out of shareable summaries.

Acceptance:

- Metrics distinguish attempted versus unique confirmed applications.
- Missing provider usage stays null; no estimated costs presented as measured.
- Synthetic sensitive markers never appear in shareable reports.

Light verification: Fixed synthetic run aggregation and privacy-canary check.

### 20. P1 — Light regression gate and clean runnable release

Issue: [#23](https://github.com/Collaboration95/auto-linkedin/issues/23) · Dependencies: #14, #22, #16, #9 · Size: Small · Risk: High · Original shortlist: 20

Add lightweight CI and clean-checkout smoke, generic quick start, synthetic demo and MIT license as the default for project-owned code (preserve third-party notices). Verify no private files/names enter public artifacts. Keep dependencies minimal.

Acceptance:

- Python tests and Node grouping tests pass in a clean synthetic workspace.
- End-to-end synthetic scenario covers discover, block, answer once, resume, submit confirmation, crash reconciliation and export recovery.
- Fresh checkout init/doctor/dashboard instructions work; final code and history/publication audit contain no private artifacts or filenames.
- Provide concise verification evidence, supported runtime and known external browser limitations.

Light verification: python3 -m unittest discover -s tests -v; node --test tests/run-grouping.test.cjs; synthetic smoke; staged/release allowlist check.

## Autonomous implementation loop

1. Read this plan, the next issue, relevant generic instructions and current working-tree state. Preserve unrelated changes and the private workspace. Check that each dependency has passed its acceptance checks.
2. Mark the issue In progress and implement the smallest coherent change. Prefer extending existing helpers; do not add unrelated features. Make routine naming/layout choices using the fixed decisions above.
3. Run the issue-specific light checks against synthetic temporary workspaces. Fix regressions before moving on. Avoid repeated broad suites when focused checks answer the question.
4. Inspect the diff and publication allowlist. Scan both filenames and content before staging; report counts and generic categories, never matched private strings. Public evidence must be synthetic or redacted.
5. Use one focused commit/review unit per issue after the privacy gate permits generic source tracking. Keep behavior integrated and runnable. If a PR is opened, register it with the thread when supported.
6. Record concise change and test evidence on the issue through gh. Move to Review while verification is pending, and Done only when integrated acceptance checks pass. Remove obsolete blocked/ready labels as dependencies resolve. Never close an issue solely because a plan or stub exists.
7. Recompute the Ready queue and continue to the next sequence item without asking for routine approval. If a required external capability is unavailable, persist its blocker, complete independent work, and report the exact limit. Never fabricate a browser or submission success.

## Milestone checkpoints

- Privacy gate: audited generic-only publication boundary and non-destructive private workspace isolation before any code publication.
- Correctness gate: intent persisted before submit; duplicate/uncertain retries blocked; answer provenance and freshness validated.
- Review gate: Questions tab aggregates stable IDs, persists one batch of answers, and requeues only genuinely unblocked jobs.
- Runner gate: durable next-action state, derived quotas, typed blockers, bounded retries/time, and recoverable exports work together.
- Release gate: clean synthetic setup and all final checks below pass. Remaining external limitations are documented accurately.

## Light final verification and definition of done

Baseline at backlog creation: 26 Python tests and 5 Node tests passed, using temporary/synthetic fixtures. This validates the existing foundation only; it does not claim planned features are implemented.

Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
node --test tests/run-grouping.test.cjs
```

Add a small synthetic smoke command as part of the release issue rather than a new test framework. Its single scenario must: initialize a clean private workspace; discover eligible and excluded jobs; encounter one shared unknown question; export/import one answer batch; resume affected jobs; fill a local test form; persist submission intent; record a synthetic confirmation; refuse a duplicate; simulate interruption and reconcile it; recover one failed export; enforce a deadline/quota; and render the resulting Questions/Run views. Verify desktop and narrow width once.

Run setup from a clean checkout/export without access to the original private workspace. Doctor must identify runtime/browser prerequisites honestly. Opening the static demo must show only fictional data. Verify relocation using relative artifact paths. Do not claim a real LinkedIn run was tested based on synthetic fixtures.

Before any publication, audit tracked/staged paths, content, generated artifacts and reachable commits. Use private comparison data locally without printing matches; keep shareable reports limited to generic findings. Fail the publication gate on a personal name, private path, real artifact filename, application record, secret, or real profile content. The final README, screenshots, CI outputs and release bundle are part of this audit.

Done means all 20 issue acceptance contracts are met; lightweight tests and the synthetic end-to-end smoke pass; the documented setup works on the declared runtime; the source tree is generic; private records remain intact and untracked; and no unnecessary service or framework was introduced. Any unavailable live-browser capability must be stated as a limitation rather than silently replaced by a mock presented as live success.

## Non-goals

Cloud/headless hosting, multi-user accounts, automatic email monitoring, paid integrations, broad ATS support, resume rewriting, elaborate analytics, custom Project-view tooling, and production-scale test infrastructure are outside this milestone. Do not expand scope to resolve an optional preference.
