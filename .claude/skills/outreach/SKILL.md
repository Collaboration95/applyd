---
name: outreach
description: Prepare draft-only follow-ups from checked submitted records; record explicit manual-send confirmations without sending messages.
---

Read [docs/outreach.md](../../../docs/outreach.md). Use the
`scripts/outreach_workspace.py` hooks inside the workspace writer lock.

- Require a separate existing run from run-start. Both frozen/current outreach
  policies must enable creation and use apply mode. Respect both deadlines,
  longer wait, smaller cap, combined do-not-contact and workspace work_guard.
- Select an existing saved contact. Never discover, scrape, visit or enrich
  profiles, guess email addresses or bypass responses, withdrawals or stops.
- Pass project/bullet IDs to the wrapper. It checks current active exact catalog
  text, submitted selected_projects fingerprints/bullet IDs and exact saved
  resume/confirmation file hashes. Missing provenance blocks; never invent text.
- Use fresh records and durable reservations. Preserve historical application
  run IDs, prepared/submitted snapshots and actual submission timestamps.
- Email supports only adapter.create_draft and an actual provider draft ID.
  Missing credentials block; no live credential probing or fabricated success.
- LinkedIn saves a local note of at most 200 UTF-16 units. The private dashboard
  offers explicit user copying and a safe profile link; the user sends manually.
- Treat draft_uncertain as a durable stop across restarts and aliases. Reconcile
  only an explicit user resolution with actual nonempty saved evidence and note.
- Record manual sends only from explicit user confirmation, preserving actual
  send time and recording time separately. A draft or copy is not a sent message.
- Keep contacts, text and evidence private. Return counts, IDs, states and
  blockers. Use synthetic fixtures and fake adapters for verification.
