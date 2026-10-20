# Export recovery

Application JSON and exact submission snapshots are authoritative. CSV and the
static dashboard are derived outputs. Capture, preparation, events and answer or
settings imports report mutation success separately from export status:

```json
{
  "mutation": {"ok": true, "application_id": "app-example"},
  "export": {"ok": false, "status": "failed", "error_code": "export_failed"}
}
```

Existing result fields remain at the top level for compatibility. An export
failure never means a submission or record mutation should be repeated. Use the
returned local retry command, or run the export-only command:

```sh
python3 scripts/workspace.py --workspace <private-root> export
```

It rebuilds outputs even when the dirty marker is absent or appears clean. It
never appends an event or replays a browser action. A failed explicit export
prints its structured outcome and exits nonzero. Failure reports omit exception
text and record contents.

For a batch, place the global deferral flag before the command, then export once
at the end, including after a stop:

```sh
python3 scripts/workspace.py --workspace <private-root> --defer-export capture --stdin
python3 scripts/workspace.py --workspace <private-root> export
```

`applications/export-state.json` stores only schema/status/dirty state. Mutation
success remains intact when an export or marker write fails. An interrupted
export leaves pending/dirty state; explicit recovery also repairs the brief crash
window between a committed record and the marker write.

A combined export reads one authoritative record snapshot and passes that same
list to CSV and dashboard generation. Explicitly empty snapshots stay empty.
Partial output is dirty until the complete rebuild succeeds. The workspace writer
lock serializes mutations and exports; deferred state survives process exit.

Verification uses temporary synthetic workspaces, injected failures, duplicate
recovery, empty snapshots and default/deferred output equivalence. No employer
actions, private data or new dependencies are involved.
