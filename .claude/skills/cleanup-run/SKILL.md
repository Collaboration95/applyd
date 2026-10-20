---
name: cleanup-run
description: Finish an applyd run with registered-tab cleanup, evidence-based reconciliation and a private summary.
---

Run from the checkout with `APPLYD_WORKSPACE` and `APPLYD_RUN_ID` set.
Use a stub browser and plan-only cleanup during implementation verification.

1. Register every tab when the run opens it: `python3 scripts/workspace.py register-tab --run-id "$APPLYD_RUN_ID" --window-id <id> --tab-id <id> --url <url> --id <app>`.
2. Preview cleanup: `python3 scripts/workspace.py cleanup-run --run-id "$APPLYD_RUN_ID"`.
3. In an authorized live run, add `--execute` to close only reverified registered tabs. Preserve uncertain submissions and moved/unrecognized tabs.
4. Review uncertain entries against actual browser/confirmation evidence. Log `submitted` only for observed confirmation; resolve a non-submission with `event --status failed --evidence <file> --note <reconciliation>`.
5. Never turn a missing confirmation into a retry automatically.
6. Run `python3 scripts/workspace.py export` and `python3 scripts/workspace.py run-summary --run-id "$APPLYD_RUN_ID"` even after a stop.
7. Return summary counts/links and unresolved blockers. An adapter failure still leaves the private summary for review.
