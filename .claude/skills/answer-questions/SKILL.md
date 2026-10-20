---
name: answer-questions
description: Review applyd's private question inbox and persist one validated batch of user-provided answers.
---

Run from the checkout with `APPLYD_WORKSPACE` set to the selected private workspace.
Use synthetic fixtures for implementation verification.

1. Read the private Questions view and affected application/occurrence links; use `python3 scripts/workspace.py context` for its current version.
2. Collect genuinely missing user facts in one batch. Retain each question's scope, type, options, units and provenance.
3. Download one answer-change file from Questions. Treat the download as pending, not persisted.
4. Preview it: `python3 scripts/workspace.py import-answers --file <change-file> --preview`.
5. Import it once: `python3 scripts/workspace.py import-answers --file <change-file>`.
6. The importer requeues jobs only after all blockers and execution checks pass. Submitted or uncertain jobs remain protected.
7. Run `python3 scripts/workspace.py export` and refresh the private dashboard. Confirm persistence from the authoritative receipt/version.
8. A stale-base or validation error requires refreshed data and a corrected change file; never bypass it or invent an answer.
