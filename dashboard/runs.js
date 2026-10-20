/* Shared by the static page and Node's built-in test runner. */
((root) => {
  "use strict";
  function groupRuns(applications) {
    const entries = [];
    for (const app of applications) {
      if (app.submissions?.length) {
        for (const sent of app.submissions) entries.push({ app, at: sent.at, kind: "submitted", status: "submitted", resume: sent.resume, applied_by: sent.applied_by || app.applied_by });
      } else {
        entries.push({ app, at: app.updated_at || app.prepared?.at || app.captured_at, kind: "pending", status: app.status, resume: app.prepared?.resume, applied_by: app.applied_by });
      }
    }
    const dated = entries.filter((entry) => Number.isFinite(Date.parse(entry.at))).sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.app.id.localeCompare(b.app.id));
    const runs = [];
    for (const entry of dated) {
      let run = runs.at(-1);
      if (!run || Date.parse(entry.at) - Date.parse(run.ended_at) > 60 * 60 * 1000) {
        run = { id: "run-" + Date.parse(entry.at), number: runs.length + 1, started_at: entry.at, ended_at: entry.at, entries: [] };
        runs.push(run);
      }
      run.entries.push(entry);
      run.ended_at = entry.at;
    }
    runs.reverse();
    const undated = entries.filter((entry) => !Number.isFinite(Date.parse(entry.at)));
    if (undated.length) runs.push({ id: "undated", number: null, started_at: null, ended_at: null, entries: undated });
    return runs;
  }
  root.JobRuns = { groupRuns };
  if (typeof module !== "undefined" && module.exports) module.exports = { groupRuns };
})(typeof window === "undefined" ? globalThis : window);
