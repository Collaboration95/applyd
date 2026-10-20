# Run cleanup

`cleanup.py` records the Brave window and tab IDs plus the URL when a run opens an application tab. The cleanup layer uses only these registrations as close candidates. A user's other tabs are never candidates.

```python
from datetime import datetime
from scripts.cleanup import register_tab, cleanup_run

register_tab(workspace, run_id, window_id, tab_id, url, application_id, datetime.now().astimezone())
result = cleanup_run(workspace, run_id, datetime.now().astimezone())  # plan only
```

The registration is stored in `applications/runs/<run-id>/tabs.json`. IDs must be positive numeric Brave IDs. Registrations are append-only: reusing an ID with different details fails.

## Plan and execute

The wrapper supports both modes:

```sh
scripts/cleanup_tabs.sh --workspace /path/to/workspace --run-id run-123
scripts/cleanup_tabs.sh --workspace /path/to/workspace --run-id run-123 --execute
```

Plan mode does not read or modify the browser. It writes a `cleanup.json` plan and both run summary files. Execute mode enumerates Brave tabs through `osascript`, checks the current tab URL against its saved URL immediately before closing, and closes by the registered window and tab IDs. Only LinkedIn and hosts in the run's `external_hosts.allow` policy are eligible. Tabs on a host outside that allowlist, tabs whose URL changed, and tabs linked to a `submission_uncertain` application are retained.

Provide an adapter with `enumerate_tabs()` and `close_tab(window_id, tab_id)` to `cleanup_run` for controlled integrations and tests. The optional `export_callback` is called once after cleanup decisions. The cleanup code does not infer or reconcile submission outcomes; uncertain entries need separate confirmation evidence and remain flagged until the application workflow records that evidence.

## Run summaries

Every invocation writes `applications/runs/<run-id>/summary.json` and `summary.md`, including runs with no application records. Summaries count submitted, skipped by reason, awaiting-answer, and failed records; include the saved listing links; and list uncertain submissions and their retained registered tabs for reconciliation.
