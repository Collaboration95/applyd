# [P2] Computer-use fallback for ATS widgets and file-upload dialogs only

## Objective
Computer-use fallback for ATS widgets and file-upload dialogs only.

## Scope
Use a computer-use agent only where browser DOM tools fail: native file pickers, canvas or custom widgets on allowed ATS hosts. Enabled only by settings (external_hosts allow), limited to the current tab, with a step budget. Every action is logged with screenshots. Not used for LinkedIn Easy Apply forms, which DOM tools handle.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Disabled by default; turning it on requires a settings key.
- [ ] Each fallback session has a step budget and screenshots, and ends in a typed outcome.

## Implementation context
skill apply-linkedin (fallback section); settings.json

## Dependencies
Blocked by: `settings` (Run settings file: Easy Apply only, external rules, save-only, dry-run, caps, blocklists), `scope-guard` (Stay on the LinkedIn page: per-run domain allowlist and off_scope stop), `kill-switch` (Account-safety stop: captcha, warnings, login walls, restriction notices)
Related existing issues: #20
Epic: Step 2 — Run control: settings, safety stops, cleanup, unattended runs. Priority: P2. Size: Medium. Risk: High.

## Light verification
Manual verification on a non-submitting test form; no live employer submission.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:computer-use -->
