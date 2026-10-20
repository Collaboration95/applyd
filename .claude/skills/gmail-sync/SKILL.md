---
name: gmail-sync
description: Reconcile explicitly authorized read-only employer mail with local applyd outcomes, preserving ambiguous matches and message evidence.
---

Use only an explicitly authorized read-only mailbox adapter exposing search, list and get. Do not send, reply, delete, label threads or create calendar entries without separate authorization.

1. With no configured adapter, report `credential_required`; do not claim a live sync.
2. Pass a supplied local export to `python3 scripts/workspace.py mail-sync --file <private-export.json>` with the selected `APPLYD_WORKSPACE` set. No file returns the typed credential blocker.
3. Preserve message IDs and offset-aware receive times. Let the helper match byte-verified confirmed applications, append idempotent outcomes and retain ambiguous messages privately.
4. Read only the count summary and typed attention reasons; do not copy mail content, contact details, application IDs or private paths to public reports.
5. Retry an empty local export to re-evaluate retained queued messages after their blockers change. Recover derived exports with `workspace.py export`; never duplicate an outcome to fix an export failure.

Before live use, configure an authorized read-only adapter and perform the manual checks in `docs/gmail-sync.md`; local fixtures do not prove live behavior.
