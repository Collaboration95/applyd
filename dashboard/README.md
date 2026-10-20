# Application run log

Double-click `index.html`. No server, installation or build step is required.

Scroll through runs, newest first. Each run lists its jobs chronologically, with timestamps, title/company, status, who applied, LinkedIn link and the exact resume sent. Details opens the saved job description and activity history.

A gap greater than one hour between consecutive application timestamps starts a new run. Exactly one hour stays in the same run; a continuous run can span several hours or cross midnight. Later interviews/rejections stay attached to the original submission. Unsent work is labelled separately and is not counted as an application.

Search and status filters keep the original run boundaries. All displayed times use Singapore time.

Application logging refreshes `data/applications.json` and `data.js` automatically. Refresh the page to see changes. To rebuild manually, run from the workspace root:

```sh
python3 scripts/workspace.py export
```

`data.js` contains the same JSON as a classic script so the page opens directly from disk. Use **Load JSON** to view one or more exported application files; matching IDs keep the latest record. Imports only change the view.

Set optional `applied_by` in job input JSON to name the intern/agent. Submitted resume links always point to the saved submission copy.
