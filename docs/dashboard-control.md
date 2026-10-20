# Dashboard control plane

Open `dashboard/index.html` directly. The dashboard uses classic local scripts and the existing exported `data.js`; it makes no requests and needs no server or build. Load one or more exported JSON files with **Load JSON**. Imported files change the current view only.

## Export integration contract

The combined exporter uses one authoritative record snapshot and enriches the private payload once:

```python
from dashboard_payload import enrich_payload

payload = enrich_payload(root, records_snapshot, payload, now)
```

`records_snapshot` is the list of raw record dictionaries already read by the exporter. `now` is optional; if supplied, it must be an offset-aware `datetime` or ISO-8601 timestamp. The helper loads validated settings, `profile/answers.json` once, and `profile/question-inbox.json` once. It does not write context, records, answer files, settings or exports, and it does not rescan application records.

It adds `settings`, `settings_hash`, `bank_version`, `answer_sources`, `answer_receipts`, `inbox`, `outcomes`, `attention` and `answer_unlock_count`. Record links are workspace-relative and only emitted for an existing saved record. Outcomes come from outcome/response fields or interview/rejection statuses. A preparing/ready record is stale after 24 hours without a status/preparation update. Attention includes every nonterminal record (including fresh discovered/preparing/ready work), every answer-blocked record, uncertain submissions and failures. Fresh work has a queued reason; preparing/ready work unchanged for 24 hours has a distinct stale reason. Each item includes a reason, application anchor and available listing/record links.

The exporter writes the final JSON and classic-script snapshot. These fields contain private application and answer data and must stay in the private workspace.

## Answer batch file

Each answer edit records a complete source replacement. Scope, source and an explicit confirmation timestamp with UTC offset are required in the form. The browser downloads one batch using the `answers.import_answers` schema:

```json
{
  "schema_version": 1,
  "import_id": "dashboard-<stable-content-id>",
  "base_version": "<exported bank_version>",
  "changes": [
    {
      "change_id": "change-q-<canonical-id>-<base-and-content-id>",
      "answer": {
        "id": "<stable answer source id>",
        "value": "<reviewed value>",
        "scope": {},
        "type": "text",
        "aliases": ["<exact question wording>"],
        "source": "<source/provenance>",
        "basis": "saved_answer",
        "confirmed_at": "2026-10-20T12:00:00+08:00"
      }
    }
  ]
}
```

The descriptor also carries the occurrence's type details (`kind`, `units`, `options`, and `skill`) and increments an explicit source version when replacing that source. Change IDs include the bank base and reviewed descriptor, so a later revision cannot reuse the earlier change ID. Option values retain their JSON scalar types. The source field starts empty and must contain user-supplied provenance; a user review has `saved_answer` basis. The core importer validates the entire batch and rejects a stale `base_version`; the CLI holds the workspace lock, imports it, runs the existing answer requeue/eligibility flow, then refreshes the export. Run copied commands from the code checkout with `APPLYD_WORKSPACE` set to the selected private data root. The command refuses to run when that selector is missing. A downloaded file remains pending, including after a browser reload. The helper recomputes occurrence resolution against the current bank without changing the saved inbox, frozen answers or record status. Canonical inbox and occurrence IDs remain stable in the export; resolving answers still requires execution checks before a job can run. The dashboard reports a persisted answer import only when refreshed data includes its receipt and current bank version.

## Settings change file

Settings are displayed from the export. The UI emits a separate change file; it never writes `settings.json`:

```json
{
  "schema_version": 1,
  "base_hash": "<exported settings_hash>",
  "changes": { "mode": "save_only" }
}
```

The corresponding CLI subcommand is `settings-import --file settings-changes.json`; copied commands require the explicit workspace environment selector. The CLI rejects stale base hashes and validates the complete resulting settings object before writing atomically. The dashboard keeps pending edits separate from the export and only reports settings confirmed after a subsequent export reflects the changed values. Copy the displayed command and replace the filename if the downloaded change file has a different name.

## Filters and display

Current status (dynamically populated), company, channel/ATS host, date range, applied-by and stop-reason filters are encoded in the URL hash. The date range uses the exported timezone. The runs tab continues to use `dashboard/runs.js`: a gap greater than one hour starts a new run, and a submitted application's later response does not move its original run. The Applications table includes a Response column for future outcome fields.

## Verification

`node --test tests/control-plane.test.cjs tests/run-grouping.test.cjs` builds the DOM test surface from the actual HTML and returns null for missing IDs. It exercises complete startup, filters, sorting, answer/settings downloads, copy commands and refreshed receipts. `python3 -m unittest discover -s tests -p 'test_dashboard*.py' -v` checks exporter behavior and imports actual browser-form batches through the Python core, including a second revised import, typed options and read-only inbox refresh.

For visual QA, `sh scripts/screenshots.sh` uses an existing Chrome/Chromium executable, a fresh temporary browser profile and synthetic records. It does not need authenticated browser state or install a browser. The setup asset list includes `control.js` alongside `app.js` and `runs.js`.

Pending browser edits and run numbering are scoped to the opened dashboard URL, so separate private dashboards do not share unsaved edits.
