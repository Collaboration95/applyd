# Lean workflows

The three repository skills call the workspace scripts. Each is under 60 lines;
answer values and role rules belong in private settings and dated answer records.
A new batch brief needs only a target and settings overrides; see
`docs/batch.example.json`.

Existing private instructions and batch records are not rewritten by installation.
The roadmap request to shrink those existing files conflicts with the requirement
to preserve every original byte. That portion requires a separate user decision
about a future, non-destructive replacement policy. The current implementation
keeps all originals and never publishes local instructions.

Use the published commands with a synthetic workspace for verification. Skills do
not grant new authorization for employer actions, messages, or notifications.
