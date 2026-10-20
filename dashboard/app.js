(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TZ = "Asia/Singapore";
  const LABELS = { discovered: "Found", preparing: "Preparing", awaiting_answer: "Needs an answer", ready: "Ready", submitted: "Applied", submission_uncertain: "Check submission", failed: "Failed", skipped: "Skipped", rejected: "Rejected", interview: "Interview", withdrawn: "Withdrawn" };
  const timeFormat = new Intl.DateTimeFormat("en-SG", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
  const dateFormat = new Intl.DateTimeFormat("en-SG", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
  const state = { data: { applications: [] }, query: "", status: "" };
  const validDate = (value) => Number.isFinite(Date.parse(value));
  const dateText = (value) => validDate(value) ? `${dateFormat.format(new Date(value))}, ${timeFormat.format(new Date(value))}` : "Time not recorded";
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function link(text, value, className) {
    if (typeof value !== "string" || !value.trim()) return null;
    let url;
    try { url = new URL(value, document.baseURI); } catch { return null; }
    if (!["https:", "http:"].includes(url.protocol) && !(url.protocol === "file:" && location.protocol === "file:" && !value.startsWith("file:"))) return null;
    const a = el("a", text, className);
    a.href = url.href; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  function pill(status) { return el("span", LABELS[status] || status || "Not recorded", `status-pill status-${Object.hasOwn(LABELS, status) ? status : "unknown"}`); }
  function loadData(payload) {
    if (!payload || !Array.isArray(payload.applications)) throw new Error("Choose an exported application JSON file.");
    const seen = new Set();
    const applications = payload.applications.map((app) => {
      if (!app || !["id", "company", "title", "status"].every((key) => typeof app[key] === "string")) throw new Error("An application is missing its ID, company, title or status.");
      if (seen.has(app.id)) throw new Error("The JSON contains duplicate application IDs.");
      seen.add(app.id);
      return { ...app, events: Array.isArray(app.events) ? app.events : [], submissions: Array.isArray(app.submissions) ? app.submissions : [] };
    });
    state.data = { ...payload, applications };
    $("error").hidden = true;
    render();
  }
  function render() {
    const allRuns = window.JobRuns.groupRuns(state.data.applications);
    const query = state.query.trim().toLowerCase();
    const visible = allRuns.map((run) => ({ ...run, entries: run.entries.filter((entry) => {
      const app = entry.app;
      return (!state.status || entry.status === state.status || app.status === state.status) && (!query || [app.title, app.company, entry.applied_by, app.location, entry.resume?.id, entry.resume?.file].filter(Boolean).join(" ").toLowerCase().includes(query));
    }) })).filter((run) => run.entries.length);
    $("runs").replaceChildren(...visible.map(runBlock));
    $("empty").hidden = visible.length > 0;
    $("empty-title").textContent = allRuns.length ? "No matching entries." : "No applications logged.";
    const sent = visible.reduce((count, run) => count + run.entries.filter((e) => e.kind === "submitted").length, 0);
    $("result-count").textContent = `${visible.length} ${visible.length === 1 ? "run" : "runs"} · ${sent} ${sent === 1 ? "application" : "applications"}`;
    $("updated").textContent = state.data.generated_at ? `Updated ${dateText(state.data.generated_at)} SGT` : "Load application JSON to get started";
  }
  function runBlock(run) {
    const section = el("section", null, "run"); section.dataset.runId = run.id;
    const header = el("header", null, "run-header");
    const title = el("div");
    title.append(el("h3", run.number === null ? "Undated entries" : `Run ${String(run.number).padStart(3, "0")}`, "run-title"));
    const span = run.started_at === run.ended_at ? dateText(run.started_at) : `${dateText(run.started_at)} → ${dateText(run.ended_at)}`;
    title.append(el("p", run.started_at ? span + " SGT" : "Timestamp missing from the saved record", "run-time"));
    const sent = run.entries.filter((entry) => entry.kind === "submitted").length;
    const pending = run.entries.length - sent;
    header.append(title, el("span", [`${sent} applied`, pending ? `${pending} other` : ""].filter(Boolean).join(" · "), "run-summary"));
    const list = el("ol", null, "run-entries");
    list.append(...run.entries.map(logEntry));
    section.append(header, list);
    return section;
  }
  function logEntry(entry) {
    const app = entry.app, resume = entry.resume;
    const item = el("li", null, "run-entry"); item.dataset.applicationId = app.id;
    item.append(el("time", validDate(entry.at) ? timeFormat.format(new Date(entry.at)) : "--:--", "entry-time"));
    const body = el("div", null, "entry-body");
    const heading = el("div", null, "entry-heading");
    heading.append(pill(entry.status), el("span", app.title, "role-title"));
    body.append(heading, el("p", [app.company, app.location].filter(Boolean).join(" · "), "company-name"));
    const metadata = el("div", null, "entry-meta");
    if (resume?.id) {
      metadata.append(el("span", entry.kind === "submitted" ? "Resume:" : "Prepared resume:"));
      metadata.append(link(resume.id, resume.url, "resume-link") || el("span", resume.id));
    } else metadata.append(el("span", "No resume submitted"));
    if (entry.applied_by) metadata.append(el("span", `By ${entry.applied_by}`, "actor"));
    if (entry.kind === "submitted" && app.status !== "submitted") metadata.append(el("span", `Now: ${LABELS[app.status] || app.status}`, "current-status"));
    body.append(metadata);
    const actions = el("div", null, "row-actions");
    const job = link(/https?:\/\/([^/]*\.)?linkedin\.com\//i.test(app.job_url || "") ? "LinkedIn ↗" : "Job post ↗", app.job_url, "job-link");
    if (job) actions.append(job);
    const details = el("button", "Details", "detail-button"); details.type = "button"; details.addEventListener("click", () => showDetails(entry)); actions.append(details);
    item.append(body, actions);
    return item;
  }
  function showDetails(entry) {
    const app = entry.app, resume = entry.resume;
    $("detail-title").textContent = app.title;
    $("detail-company").textContent = [app.company, app.location].filter(Boolean).join(" · ");
    $("detail-meta").replaceChildren(pill(entry.status), el("span", dateText(entry.at) + " SGT"), el("span", `Applied by: ${entry.applied_by || "Not recorded"}`));
    $("detail-links").replaceChildren(...[link("Original job post ↗", app.job_url), link("Employer application page ↗", app.application_url), resume?.url ? link(`${entry.kind === "submitted" ? "Submitted" : "Prepared"} resume ↗`, resume.url) : null].filter(Boolean));
    $("detail-description").textContent = app.description || "No job description was saved for this application.";
    $("detail-notes").textContent = app.notes || ""; $("detail-notes-section").hidden = !app.notes;
    $("detail-events").replaceChildren(...[...app.events].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).map((event) => {
      const item = el("li"); item.append(el("time", dateText(event.at) + " SGT"), el("span", LABELS[event.status] || event.status));
      if (event.note) item.append(el("p", event.note));
      return item;
    }));
    $("details").showModal();
  }
  $("search").addEventListener("input", (event) => { state.query = event.target.value; render(); });
  $("status").addEventListener("change", (event) => { state.status = event.target.value; render(); });
  $("refresh").addEventListener("click", () => location.reload());
  $("close-details").addEventListener("click", () => $("details").close());
  document.querySelector(".import-button").addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); $("import").click(); } });
  $("import").addEventListener("change", async (event) => {
    try {
      const files = [...event.target.files]; if (!files.length) return;
      const payloads = await Promise.all(files.map(async (file) => JSON.parse(await file.text())));
      const merged = new Map();
      for (const payload of payloads) {
        if (!Array.isArray(payload.applications)) throw new Error("Choose exported application JSON files.");
        for (const app of payload.applications) {
          const old = merged.get(app.id);
          if (!old || new Date(app.updated_at || 0) >= new Date(old.updated_at || 0)) merged.set(app.id, app);
        }
      }
      loadData({ generated_at: payloads.map((p) => p.generated_at).filter(Boolean).sort().at(-1), applications: [...merged.values()] });
    } catch (error) { $("error").textContent = `Could not load JSON: ${error.message}`; $("error").hidden = false; }
    finally { event.target.value = ""; }
  });
  window.JobJournal = { loadData };
  try { loadData(window.JOB_SEARCH_DATA || { applications: [] }); }
  catch (error) { $("error").textContent = error.message; $("error").hidden = false; }
})();
