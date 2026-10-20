/* Shared by the static page and Node's built-in test runner. */
((root) => {
  "use strict";
  function normalizeApplications(applications) {
    const normalized = [], seen = new Set();
    let skipped = 0;
    for (const app of applications) {
      if (!app || typeof app !== "object" || Array.isArray(app) || !["id", "company", "title", "status"].every((key) => typeof app[key] === "string" && app[key].trim()) || seen.has(app.id)) {
        skipped++;
        continue;
      }
      seen.add(app.id);
      const events = Array.isArray(app.events) ? app.events.filter((event) => event && typeof event === "object" && !Array.isArray(event)) : [];
      const submissions = Array.isArray(app.submissions) ? app.submissions.filter((submission) => submission && typeof submission === "object" && !Array.isArray(submission)) : [];
      normalized.push({ ...app, events, submissions });
    }
    return { applications: normalized, skipped };
  }
  function availableStatuses(applications) {
    return [...new Set(applications.map((app) => app.status))];
  }
  function matchesCurrentStatus(app, selected) {
    return !selected || app.status === selected;
  }
  function assignRunNumbers(runs, saved = {}) {
    const mapping = { ...saved };
    const dated = [...runs].filter((item) => item.id !== "undated").sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
    for (let index = 0; index < dated.length; index++) {
      const run = dated[index];
      const priorEntry = run.entries.map(entry => mapping[`entry:${entry.app.id}:${entry.at}`]).find(Number.isFinite);
      if (!Number.isFinite(mapping[run.id]) && Number.isFinite(priorEntry)) mapping[run.id] = priorEntry;
      if (!Number.isFinite(mapping[run.id])) {
        let previous, next;
        for (let i = index - 1; i >= 0 && previous === undefined; i--) if (Number.isFinite(mapping[dated[i].id])) previous = mapping[dated[i].id];
        for (let i = index + 1; i < dated.length && next === undefined; i++) if (Number.isFinite(mapping[dated[i].id])) next = mapping[dated[i].id];
        if (previous === undefined && next === undefined) mapping[run.id] = 1;
        else if (previous === undefined) mapping[run.id] = next - 1;
        else if (next === undefined) mapping[run.id] = previous + 1;
        else mapping[run.id] = (previous + next) / 2;
      }
      run.number = mapping[run.id];
      for (const entry of run.entries) mapping[`entry:${entry.app.id}:${entry.at}`] = run.number;
    }
    return mapping;
  }
  function mergePayloads(payloads) {
    const merged = new Map(), malformed = [];
    for (const payload of payloads) {
      if (!payload || !Array.isArray(payload.applications)) throw new Error("Choose exported application JSON files.");
      for (const app of payload.applications) {
        if (!app || typeof app !== "object" || typeof app.id !== "string") { malformed.push(app); continue; }
        const old = merged.get(app.id);
        if (!old || Date.parse(app.updated_at || 0) >= Date.parse(old.updated_at || 0)) merged.set(app.id, app);
      }
    }
    const latest = [...payloads].sort((a, b) => String(a.generated_at || "").localeCompare(String(b.generated_at || ""))).at(-1) || {};
    return { ...Object.assign({}, ...payloads), ...latest, generated_at: payloads.map((p) => p.generated_at).filter(Boolean).sort().at(-1), applications: [...merged.values(), ...malformed] };
  }
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
  root.JobRuns = { groupRuns, normalizeApplications, availableStatuses, matchesCurrentStatus, assignRunNumbers, mergePayloads };
  if (typeof module !== "undefined" && module.exports) module.exports = { groupRuns, normalizeApplications, availableStatuses, matchesCurrentStatus, assignRunNumbers, mergePayloads };
})(typeof window === "undefined" ? globalThis : window);
