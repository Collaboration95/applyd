# Roadmap v2 — local issue drafts

Create epics first, then add each issue as a sub-issue of its epic. `#N` refers to existing GitHub issues; other dependencies are v2 keys.

## Step 1 — [Step 1 — Stability: fast, deterministic, scoped execution](issues/epic-stability.md)

- [P0] [Ultrafast already-seen check with marker files and seen.sh](issues/stability-01-seen-index.md) — blocked by: None
- [P0] [Deterministic LinkedIn job-page extractor + capture --stdin](issues/stability-02-extractor.md) — blocked by: `seen-index` (Ultrafast already-seen check with marker files and seen.sh)
- [P1] [Stay on the LinkedIn page: per-run domain allowlist and off_scope stop](issues/stability-03-scope-guard.md) — blocked by: `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin), `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
- [P1] [prepare-answers: build each application's answer snapshot from the saved answer bank](issues/stability-04-prepare-answers.md) — blocked by: None
- [P1] [Dashboard robustness and housekeeping](issues/stability-05-dashboard-robust.md) — blocked by: None
- [P1] [Three lean skills that call scripts instead of restating rules](issues/stability-06-skills.md) — blocked by: `seen-index` (Ultrafast already-seen check with marker files and seen.sh), `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin), `prepare-answers` (prepare-answers: build each application's answer snapshot from the saved answer bank), `cleanup-run` (End-of-run cleanup: close only the tabs this run opened, reconcile, summarise)

## Step 2 — [Step 2 — Run control: settings, safety stops, cleanup, unattended runs](issues/epic-control.md)

- [P0] [Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists](issues/control-01-settings.md) — blocked by: None
- [P0] [Account-safety stop: captcha, warnings, login walls, restriction notices](issues/control-02-kill-switch.md) — blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
- [P1] [End-of-run cleanup: close only the tabs this run opened, reconcile, summarise](issues/control-03-cleanup-run.md) — blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
- [P2] [Scheduled unattended runs with an end-of-run notification](issues/control-04-schedule.md) — blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists), `kill-switch` (Account-safety stop: captcha, warnings, login walls, restriction notices), `cleanup-run` (End-of-run cleanup: close only the tabs this run opened, reconcile, summarise), `skills` (Three lean skills that call scripts instead of restating rules)
- [P2] [Pre-submit reviewer agent compares the filled form with the answer snapshot](issues/control-05-review-agent.md) — blocked by: `prepare-answers` (prepare-answers: build each application's answer snapshot from the saved answer bank)
- [P2] [Computer-use fallback for ATS widgets and file-upload dialogs only](issues/control-06-computer-use.md) — blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists), `scope-guard` (Stay on the LinkedIn page: per-run domain allowlist and off_scope stop), `kill-switch` (Account-safety stop: captcha, warnings, login walls, restriction notices)

## Step 2 — [Step 2 — Feedback: dashboard control plane, outcomes, contacts](issues/epic-feedback.md)

- [P1] [Dashboard control plane: stat bar, needs-attention, funnel, tabs, filters](issues/feedback-01-dashboard-v2.md) — blocked by: `dashboard-robust` (Dashboard robustness and housekeeping)
- [P1] [Outcome tracking: interview, assessment, rejection, offer, follow-up dates](issues/feedback-02-outcomes.md) — blocked by: None
- [P2] [Read-only Gmail sync: classify replies into outcomes](issues/feedback-03-gmail-sync.md) — blocked by: `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates)
- [P2] [Store hiring-team contacts visible on the job page](issues/feedback-04-contacts.md) — blocked by: `extractor` (Deterministic LinkedIn job-page extractor + capture --stdin)
- [P2] [Draft-only follow-ups: Gmail drafts and copyable LinkedIn notes](issues/feedback-05-outreach-drafts.md) — blocked by: `contacts` (Store hiring-team contacts visible on the job page), `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates), `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists)
- [P2] [Weekly report: volume, response rates by preset/role/channel, effort](issues/feedback-06-weekly-report.md) — blocked by: `outcomes` (Outcome tracking: interview, assessment, rejection, offer, follow-up dates)

## Step 2 — [Step 2 — Public repo presence](issues/epic-public.md)

- [P1] [Synthetic demo dataset and automated screenshots](issues/public-01-demo-data.md) — blocked by: #4
- [P1] [Public README, setup guide, architecture diagram, repo description](issues/public-02-readme.md) — blocked by: `demo-data` (Synthetic demo dataset and automated screenshots), #4
- [P2] [Mascot sprite and banner](issues/public-03-sprite.md) — blocked by: None
