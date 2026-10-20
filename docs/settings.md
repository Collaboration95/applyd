# Workspace settings

Copy `settings.example.json` to `settings.json` in the selected private workspace.
Keep personal configuration and run snapshots private. No dependency installation
or browser actions are needed by this module.

The parent CLI selects a workspace using `--workspace` first, then
`APPLYD_WORKSPACE`, then the existing local layout. Pass that root explicitly to
every settings helper. The settings module never consults the environment or
another workspace. A missing `settings.json` in an existing root uses compatible
defaults without writing files. A missing root, invalid JSON, duplicate key,
unknown key, unsupported schema or invalid value raises `SettingsError`
(`ValueError`) with corrective guidance. Supplied partial objects receive defaults.

| Setting | Schema 1 behavior/default |
| --- | --- |
| `schema_version` | Integer `1` |
| `mode` | `apply` (default), `save_only`, `dry_run` |
| `easy_apply_only` | Boolean, default `false` for existing workspaces |
| `external_hosts` | `allow`/`deny` hostname arrays, default empty; empty allow permits hosts; deny wins. Bare host matches itself and subdomains, case-insensitively, never a suffix lookalike. Applies to external destinations. |
| `daily_cap`, `per_run_cap` | Non-negative integer, or `null` for unlimited; `0` blocks submission |
| `per_run_minutes` | Non-negative integer elapsed minutes, or `null`; deadline boundary blocks |
| `company_blocklist`, `title_blocklist` | Arrays of case-insensitive Python regular expressions, default empty |
| `max_required_years` | Non-negative integer or `null`; rejects a known hard minimum above the limit |
| `retryable_statuses` | Defaults to `failed`, `awaiting_answer`; non-terminal statuses only; never bypasses reconciliation |
| `start_mode` | `recommended` (default), `saved`, or HTTPS LinkedIn `/jobs/search…` URL |
| `timezone` | Installed IANA name, default `UTC`; controls run timestamps and daily boundaries |
| `follow_up_days` | Non-negative days, default `14`, for response/follow-up attention |
| `browser`, `resume_scope` | Optional non-empty private strings, default `null` |
| `role_rules` | Private JSON object, default `{}`; interpreted by parent role-policy integration |
| `computer_use` | Object with only `enabled` (boolean, default `false`) and `step_budget` (non-negative integer, default `6`); omitted nested values receive defaults. No null/unlimited budget; `0` permits no action. |
| `outreach` | Object with only `enabled` (boolean, default `false`), `business_days` (non-negative integer, default `10`), `daily_draft_cap` (non-negative integer, default `3`) and `do_not_contact` (nonempty-string array, default `[]`). Partial objects receive defaults; null, unknown keys and boolean numbers fail. |

The example contains every default and no personal facts. `browser`,
`resume_scope`, and `role_rules` are preserved in snapshots; this module does not
launch a browser, select resumes or infer personal qualifications. In a fresh
workspace the parent must validate missing profile/resume prerequisites rather
than treating compatible settings defaults as permission or application readiness.

# Parent integration contract

1. After selecting the root, call `load_settings(root)` on **every command**.
   Construct timestamps using `ZoneInfo(settings["timezone"])`; helpers accept an
   aware `datetime` in any timezone and reject naive clocks. Existing timezone
   hardcoding in CLI/export/resolver remains the parent's integration work.
2. At run start call `run_start(root, run_id, now)`. This creates only
   `applications/runs/<run_id>/run.json` with the full effective settings,
   canonical SHA-256 `settings_hash`, `started_at`, and `deadline` (`null` when
   unlimited). It atomically publishes without overwriting an existing ID.
   Resume with `load_run(root, run_id)`; never restart the same ID to reset limits.
   Hash/deadline checks detect accidental modifications; they are not signatures
   against an actor who can rewrite the entire private workspace.
3. Before opening an external application, extracting the full description or
   touching a form, call `eligibility(header, settings)`. It returns `None` to
   continue, or a stable reason: `skipped:external_apply`, `skipped:blocklist`,
   `skipped:invalid_application_url`, `skipped:external_host`, or
   `skipped:required_years`, in that precedence. Persist skip status/reason with
   the header. For a resumed run, evaluate both current and frozen settings.
   Header keys: `company`, `role` (or `title`), `application_url` (fallback
   `listing_url`/`url`), `easy_apply`/`is_easy_apply` booleans or
   `apply_type: "external"`, and optional numeric `required_years`.
   A non-LinkedIn destination also establishes an external application. Missing
   header facts are not inferred; obtain them before opening any destination.
   Unknown host/experience is not proof of eligibility. Only pass
   `required_years` for a known hard minimum; ambiguity belongs in review.
4. `save_only` captures listings and may use an explicitly supported Save action;
   it does not apply. `dry_run` may fill a form, never clicks Submit, and the
   parent records `dry_run_complete`. These execution/status transitions remain
   parent work. Retry/start settings likewise require runner integration; they
   do not authorize retrying confirmed or uncertain submissions.
5. At ready preparation, persist optional `run_id` on the application and its
   `prepared` snapshot. Preserve that ID on every confirmed `submissions` entry.
   A legacy ready record without a run works with unlimited per-run settings;
   configured per-run limits require a run. Conflicting record/prepared IDs fail.
6. Immediately before Submit, inside the existing single-writer critical
   section, call `check_submission(root, record, now)` **in addition to** existing
   resume/answer, duplicate/uncertainty, consent and observed-job identity gates.
   `SettingsError` blocks the click; success returns `ready`, counts and settings
   hashes. Both current and frozen policies must pass. Edits can tighten a mode,
   host/blocklist, experience limit, cap or deadline; they cannot relax a frozen
   restriction. Current time limits run from the original frozen start.
   The gate does not reserve a quota slot or replace durable submission intent.
7. Log observed confirmations even after settings/caps change. Apply settings
   gates to a future click, never use them to discard historical success.

Counters read `applications/records/*/record.json` in the selected root, using
confirmed `submissions[].submitted_at` and the legacy top-level `submitted_at`.
Follow-up statuses retain their original confirmations. Saved, dry-run, ready,
failed and uncertain attempts without a confirmation timestamp do not count.
Each unique application counts once in a day/run, including duplicate history
entries and cross-posts linked by application ID, platform/job ID, employer
requisition/company, canonical listing URL, or company/role/location. Corrupt
records fail closed. Daily limits use each policy's timezone separately. Run
counts use the original submission's `run_id`; legacy top-level-only confirmations
use their top-level ID. History entries with no ID are daily counts only and must
not be reassigned to a later run. Offsets survive relocation; no root paths occur
in the run snapshot. Elapsed deadlines remain correct across daylight saving.

`settings_hash(settings)` hashes the validated complete snapshot as UTF-8 JSON
with sorted keys, compact separators and no ASCII escaping. Defaults are included;
changing JSON object order or moving the workspace does not change the hash.

Verification: `python3 -m unittest discover -s tests -p test_settings.py -v`.
The parent owns CLI glue and the publication allowlist for `settings.example.json`.

Immutable schema-1 run hashes are verified against their exact saved settings. Newly introduced optional defaults do not rewrite or invalidate older run snapshots; original required policy fields remain mandatory.

Computer-use fallback uses `computer_fallback.run_workspace_session` to intersect
current and verified frozen settings at each adapter boundary. Both must enable
fallback and explicitly allow the exact pinned origin's host; deny wins, the
smaller budget applies, and either deadline or Easy Apply restriction blocks use.
Old snapshots without `computer_use` remain disabled. This does not change the
ordinary external-host rule: fallback alone requires a non-empty explicit allow.
The required live guard and adapter contract are in [computer-use.md](computer-use.md).
`validate_settings(raw)` exposes the same strict, default-filling validation as
`load_settings(root)` without filesystem access.

Outreach uses `outreach_workspace.create_follow_up` inside the workspace writer
lock and requires a dedicated existing run from `run-start`. Both current and
frozen policies must enable outreach and use `apply` mode. The longer business-day
wait, smaller draft cap and combined do-not-contact entries apply; both timezones'
daily boundaries and both deadlines are checked. Old snapshots without outreach
stay disabled without rewriting their bytes or hashes. Outreach reservations carry
their own run ID; historical application/submission run IDs stay unchanged.
See [outreach.md](outreach.md) for file/provenance checks and command hooks.
