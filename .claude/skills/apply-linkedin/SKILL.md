---
name: apply-linkedin
description: Run the local applyd listing and application workflow within an existing user authorization, using deterministic workspace commands.
---

Run commands from the code checkout with `APPLYD_WORKSPACE` and `APPLYD_RUN_ID` set to the selected private workspace and durable run ID.
Use synthetic fixtures and stub browser actions when verifying implementation.

1. Start a new run with `python3 scripts/workspace.py run-start --id "$APPLYD_RUN_ID"`; resume an existing ID without resetting its limits.
2. Read the settings/context output. Run `scripts/seen.sh <identity>...` before opening result cards; use only new or policy-retryable entries.
3. Screen result-card headers with `workspace.py screen --file <adapter-header.json>` before extraction or destination navigation.
4. Job facts come only from the current LinkedIn job-page DOM. Evaluate `scripts/extract_linkedin.js` and pipe its exact JSON to `workspace.py capture --stdin --run-id "$APPLYD_RUN_ID"`.
5. Before every destination navigation, call `workspace.py scope-check --id <app> --url <destination>`. An off_scope refusal ends that job. Do not browse company sites, profiles or other sources for facts.
6. On an account_safety signal, capture the current page privately and call `workspace.py safety-check --run-id "$APPLYD_RUN_ID" --file <page-snapshot.json> --screenshot <private-reference>`; end the entire run. A STOP/safety refusal also ends the run.
7. Pass exact observed questions to `workspace.py prepare-answers --id <app> --questions <adapter-questions.json>`. Leave unresolved jobs waiting and continue independent jobs.
8. Resolve the selected resume immediately before upload: `python3 scripts/resolve_resume.py --scope <configured-scope> --purpose application`.
9. Freeze the returned answer file with `workspace.py event --id <app> --status ready --resume-scope <scope> --answers <answers-path> --run-id "$APPLYD_RUN_ID"`.
10. In dry_run mode record `dry_run_complete` without Submit. In save_only mode stop after capture.
11. For an authorized apply-mode submission, run `workspace.py preflight --id <app>`, then `workspace.py begin-submit --id <app> --observed-url <current-job-url> --session-id <writer-id>` immediately before the one Submit click.
12. Record `submitted` only after observed confirmation using `workspace.py event --id <app> --status submitted --evidence <private-confirmation-file>`. An interrupted click remains uncertain; reconcile with evidence before any retry.

Use the `python3 scripts/workspace.py` prefix for commands abbreviated as `workspace.py` above.
