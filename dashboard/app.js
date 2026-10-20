(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const C = window.DashboardControl;
  const STORAGE_PREFIX = "applyd:" + document.baseURI + ":";
  const WORKSPACE_CLI = 'python3 scripts/workspace.py --workspace "${APPLYD_WORKSPACE:?Set APPLYD_WORKSPACE for this export}"';
  const LABELS = { discovered: "Found", preparing: "Preparing", awaiting_answer: "Needs an answer", ready: "Ready", submitted: "Applied", submission_uncertain: "Check submission", failed: "Failed", skipped: "Skipped", rejected: "Rejected", interview: "Interview", withdrawn: "Withdrawn", saved: "Saved", dry_run_complete: "Dry run complete", acknowledged: "Acknowledged", assessment: "Assessment", offer: "Offer" };
  const state = { data: { applications: [] }, applications: [], filters: {}, sort: { key: "submitted_at", direction: "desc" }, pending: [], settingsChanges: {}, skipped: 0, tab: "applications" };
  try { state.lastAnswerImport = JSON.parse(localStorage.getItem(STORAGE_PREFIX + "pendingAnswerImport") || "null"); state.lastSettingsDesired = JSON.parse(localStorage.getItem(STORAGE_PREFIX + "pendingSettingsDesired") || "null"); } catch {}
  let TZ = "UTC", dateFormat, timeFormat;
  const fmt = () => {
    dateFormat = new Intl.DateTimeFormat("en-SG", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
    timeFormat = new Intl.DateTimeFormat("en-SG", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
  };
  const validDate = (v) => Number.isFinite(Date.parse(v));
  const dateText = (v) => validDate(v) ? `${dateFormat.format(new Date(v))}, ${timeFormat.format(new Date(v))}` : "Time not recorded";
  function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = text; if (cls) n.className = cls; return n; }
  function safeLink(text, value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try { const u = new URL(value, document.baseURI); if (!["https:", "http:"].includes(u.protocol) && !(u.protocol === "file:" && location.protocol === "file:" && !value.startsWith("file:"))) return null; const a = el("a", text); a.href = u.href; a.target = "_blank"; a.rel = "noopener noreferrer"; return a; } catch { return null; }
  }
  function pill(status) { return el("span", LABELS[status] || status || "Not recorded", `status-pill status-${status || "unknown"}`); }
  function appAnchor(id) { return `#application-${encodeURIComponent(id)}`; }
  function anchor(text, href) {
    const a = el("a", text); a.href = href;
    if (href.startsWith("#application-")) a.addEventListener("click", (event) => { event.preventDefault(); history.replaceState(null, "", href); openApplicationHash(); });
    return a;
  }
  function setError(message) { $("error").textContent = message || ""; $("error").hidden = !message; }
  function loadData(payload) {
    if (!payload || !Array.isArray(payload.applications)) throw new Error("Choose an exported application JSON file.");
    TZ = typeof payload.timezone === "string" ? payload.timezone : "UTC";
    try { new Intl.DateTimeFormat("en", { timeZone: TZ }); } catch { TZ = "UTC"; }
    fmt();
    const normalized = window.JobRuns.normalizeApplications(payload.applications);
    state.data = { ...payload, applications: normalized.applications };
    state.applications = normalized.applications; state.skipped = normalized.skipped;
    setError("");
    renderStatusOptions();
    if (state.lastAnswerImport && JSON.stringify(state.pending) === state.lastAnswerImport.changes && (state.data.answer_receipts || []).some((r) => r.import_id === state.lastAnswerImport.import_id && r.bank_version === state.data.bank_version)) state.pending = [];
    for (const [key, value] of Object.entries(state.settingsChanges)) if (JSON.stringify(state.data.settings?.[key]) === JSON.stringify(value)) delete state.settingsChanges[key];
    renderAll();
  }
  function renderStatusOptions() {
    const select = $("filter-status");
    const statuses = window.JobRuns.availableStatuses(allRecords()).sort();
    if (state.filters.status && !statuses.includes(state.filters.status)) delete state.filters.status;
    select.replaceChildren(new Option("All statuses", ""), ...statuses.map((status) => new Option(LABELS[status] || status, status)));
    select.value = state.filters.status || "";
  }
  function allRecords() { return state.data.applications || []; }
  function filtered() { return C.filterApplications(allRecords(), state.filters, state.data.timezone || TZ); }
  function renderStats() {
    const counts = C.stats(allRecords(), new Date(), TZ);
    $("stats").replaceChildren(...Object.entries(counts).map(([key, value]) => { const card = el("div", null, "stat"); card.append(el("strong", String(value)), el("span", key)); return card; }));
    const funnel = C.funnel(allRecords(), state.data.outcomes || []);
    $("funnel").replaceChildren(...Object.entries(funnel).map(([key, value]) => { const row = el("div", null, "funnel-item"); row.append(el("span", key), el("strong", String(value))); return row; }));
  }
  function renderAttention() {
    const items = state.data.attention || C.attention(allRecords(), state.data.timezone || TZ, new Date());
    const list = $("attention-items");
    list.replaceChildren(...items.map((item) => {
      const row = el("article", null, "attention-item"); row.append(pill(item.status), el("strong", `${item.company || "Company not recorded"} · ${item.title || "Role not recorded"}`), el("p", item.reason || item.stop_reason || "Needs review."));
      if (item.stale) row.append(el("span", "Stale", "status-pill"));
      const links = el("div", null, "inline-links"); links.append(anchor("Open application", appAnchor(item.application_id || item.id)));
      const job = safeLink("Original listing ↗", item.listing_url || item.job_url); if (job) links.append(job);
      const record = safeLink("Saved record ↗", item.record_url); if (record) links.append(record);
      row.append(links); return row;
    }));
    $("attention-empty").hidden = items.length > 0;
  }
  function renderApplications() {
    const apps = filtered();
    document.querySelectorAll("th[data-sort]").forEach((th) => th.setAttribute("aria-sort", th.dataset.sort === state.sort.key ? (state.sort.direction === "asc" ? "ascending" : "descending") : "none"));
    const table = $("application-rows");
    table.replaceChildren(...C.sortApplications(apps, state.sort.key, state.sort.direction).map((app) => {
      const tr = el("tr"); tr.id = `application-${app.id}`; tr.dataset.applicationId = app.id;
      const title = el("td"); title.append(el("strong", app.title), el("small", app.company));
      const status = el("td"); status.append(pill(app.status));
      const channel = el("td", C.channel(app));
      const submitted = C.latestSubmission(app);
      const applied = el("td", submitted ? dateText(submitted.at) : "—");
      const response = el("td", app.response || app.outcome || "—");
      const actions = el("td"); const detail = el("button", "Details", "small-button"); detail.type = "button"; detail.addEventListener("click", () => showDetails(app)); actions.append(detail);
      const job = safeLink("Listing ↗", app.job_url); if (job) actions.append(job);
      tr.append(title, status, channel, applied, response, el("td", app.applied_by || "—"), actions); return tr;
    }));
    $("application-count").textContent = `${apps.length} of ${allRecords().length} applications`;
    $("application-empty").hidden = apps.length > 0;
  }
  function renderRuns() {
    const visibleIds = new Set(filtered().map((app) => app.id));
    const allRuns = window.JobRuns.groupRuns(allRecords());
    try { state.runNumbers = { ...JSON.parse(localStorage.getItem(STORAGE_PREFIX + "runNumbers") || "{}"), ...(state.runNumbers || {}) }; } catch { state.runNumbers ||= {}; }
    state.runNumbers = window.JobRuns.assignRunNumbers(allRuns, state.runNumbers);
    try { localStorage.setItem(STORAGE_PREFIX + "runNumbers", JSON.stringify(state.runNumbers)); } catch {}
    const runs = allRuns.map((run) => ({ ...run, entries: run.entries.filter((entry) => visibleIds.has(entry.app.id)) })).filter((run) => run.entries.length);
    $("runs").replaceChildren(...runs.map((run) => {
      const section = el("section", null, "run"); const header = el("header", null, "run-header");
      const title = el("div"); title.append(el("h3", run.number === null ? "Undated entries" : `Run ${String(run.number).padStart(3, "0")}`, "run-title"));
      const span = run.started_at === run.ended_at ? dateText(run.started_at) : `${dateText(run.started_at)} → ${dateText(run.ended_at)}`;
      title.append(el("p", run.started_at ? span : "Timestamp missing from the saved record", "run-time"));
      const applied = run.entries.filter((x) => x.kind === "submitted").length;
      header.append(title, el("span", `${applied} applied · ${run.entries.length - applied} other`, "run-summary"));
      const list = el("ol", null, "run-entries");
      for (const entry of run.entries) {
        const app = entry.app, row = el("li", null, "run-entry"); row.dataset.applicationId = app.id;
        row.append(el("time", validDate(entry.at) ? timeFormat.format(new Date(entry.at)) : "--:--", "entry-time"));
        const body = el("div"); const heading = el("div", null, "entry-heading"); heading.append(pill(entry.status), el("span", app.title, "role-title"));
        body.append(heading, el("p", [app.company, app.location].filter(Boolean).join(" · "), "company-name"));
        const meta = el("div", null, "entry-meta"); meta.append(el("span", `By ${entry.applied_by || app.applied_by || "Not recorded"}`));
        const resume = entry.resume; if (resume?.id) { meta.append(el("span", "Resume:"), safeLink(resume.id, resume.url) || el("span", resume.id)); }
        body.append(meta); const actions = el("div", null, "row-actions"); const open = el("button", "Details", "small-button"); open.type = "button"; open.addEventListener("click", () => showDetails(app)); actions.append(open); const listing = safeLink("Listing ↗", app.job_url); if (listing) actions.append(listing); row.append(body, actions); list.append(row);
      }
      section.append(header, list); return section;
    }));
    $("run-count").textContent = `${runs.length} ${runs.length === 1 ? "run" : "runs"}`;
    $("runs-empty").hidden = runs.length > 0;
  }
  function showDetails(app) {
    $("detail-title").textContent = app.title; $("detail-company").textContent = [app.company, app.location].filter(Boolean).join(" · ");
    const sub = C.latestSubmission(app); $("detail-meta").replaceChildren(pill(app.status), el("span", sub ? dateText(sub.at) : "Not submitted"), el("span", `Applied by: ${sub?.applied_by || app.applied_by || "Not recorded"}`));
    const links = [safeLink("Original listing ↗", app.job_url), safeLink("Application page ↗", app.application_url), safeLink("Saved record ↗", app.record_url)].filter(Boolean); $("detail-links").replaceChildren(...links);
    $("detail-description").textContent = app.description || "No saved job description.";
    renderContacts(app.contacts);
    renderOutreach(app.outreach, app.outreach_blocker);
    $("detail-events").replaceChildren(...[...(app.events || [])].sort((a,b) => Date.parse(b.at) - Date.parse(a.at)).map((event) => { const li = el("li"); li.append(el("time", dateText(event.at)), el("span", LABELS[event.status] || event.status)); if (event.note) li.append(el("p", event.note)); return li; }));
    $("details").showModal();
  }
  function contactProfileLink(value) {
    if (typeof value !== "string" || !/^https?:\/\/[^/]/i.test(value) || /[\s\x00-\x1f\x7f\\]/.test(value)) return null;
    try {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
      return safeLink(value, value);
    } catch { return null; }
  }
  function renderContacts(contacts) {
    let section = $("detail-contacts");
    if (!section) { section = el("section"); section.id = "detail-contacts"; $("details").append(section); }
    section.replaceChildren(el("h3", "Contacts"));
    const entries = Array.isArray(contacts) ? contacts.filter((contact) => contact && typeof contact === "object" && !Array.isArray(contact)) : [];
    if (!entries.length) { section.append(el("p", "No saved contacts.")); return; }
    const list = el("ul");
    for (const contact of entries) {
      const item = el("li"), facts = el("dl");
      for (const [key, label] of [["name", "Name"], ["headline", "Headline"], ["profile_url", "Profile"], ["email", "Email"], ["source", "Source"], ["captured_at", "Captured at"], ["relationship", "Relationship"], ["contact_id", "Contact ID"]]) {
        const value = typeof contact[key] === "string" ? contact[key] : null;
        const detail = el("dd");
        detail.append(key === "profile_url" ? (contactProfileLink(value) || el("span", value || "Not recorded")) : el("span", value || "Not recorded"));
        facts.append(el("dt", label), detail);
      }
      facts.append(el("dt", "Verification"), el("dd", contact.verified === false ? "Unverified" : "Not recorded"));
      item.append(facts); list.append(item);
    }
    section.append(list);
  }
  function outreachProfileLink(value) {
    const link = contactProfileLink(value);
    if (!link) return null;
    const url = new URL(value), host = url.hostname.toLowerCase();
    if ((host !== "linkedin.com" && !host.endsWith(".linkedin.com")) || !/^\/(in|pub)\/[^/]+(?:\/[^/]+)*\/?$/.test(url.pathname)) return null;
    link.textContent = "LinkedIn profile ↗";
    return link;
  }
  function renderOutreach(outreach, blocker) {
    let section = $("detail-outreach");
    if (!section) { section = el("section"); section.id = "detail-outreach"; $("details").append(section); }
    section.replaceChildren(el("h3", "Outreach"));
    if (typeof blocker?.reason === "string") section.append(el("p", `Blocked: ${blocker.reason}`));
    const entries = Array.isArray(outreach) ? outreach.filter((e) => e && typeof e === "object" && !Array.isArray(e)) : [];
    if (!entries.length) { section.append(el("p", "No saved outreach.")); return; }
    const labels = { draft_created: "Email draft created · not sent", note_created: "LinkedIn note saved · not sent", draft_uncertain: "Draft uncertain · reconcile before retry", not_created: "Confirmed not created" };
    for (const event of entries) {
      const item = el("article"); item.append(el("p", labels[event.status] || "Outreach status unavailable"));
      item.append(el("time", dateText(event.reserved_at)));
      const text = typeof event.text === "string" ? event.text : "";
      if (event.channel === "linkedin") {
        // JavaScript string length counts UTF-16 units, matching the helper.
        if (text && text.length <= 200) {
          item.append(el("p", text, "outreach-note"));
          if (event.status === "note_created") {
            const copy = el("button", "Copy note", "small-button"); copy.type = "button";
            copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy note"; } });
            item.append(copy);
          }
        } else item.append(el("p", "Note unavailable or exceeds 200 UTF-16 units."));
        const link = outreachProfileLink(event.profile_url); if (link) item.append(link);
      } else if (event.channel === "email") {
        if (typeof event.subject === "string") item.append(el("p", event.subject));
        if (text) item.append(el("p", text));
      }
      for (const send of Array.isArray(event.manual_sends) ? event.manual_sends : []) {
        if (send && typeof send === "object") item.append(el("p", `Manual send confirmed: ${dateText(send.occurred_at)} · recorded ${dateText(send.recorded_at)}`));
      }
      section.append(item);
    }
  }
  function renderQuestions() {
    const inbox = state.data.inbox || { questions: [] }, questions = inbox.questions || [];
    const container = $("questions-list");
    container.replaceChildren(...questions.map((q) => questionCard(q)));
    $("question-count").textContent = `${questions.filter((q) => q.status !== "resolved" && q.status !== "answered").length} unresolved question groups · ${state.data.answer_unlock_count || 0} jobs with all answer blockers resolved`;
    $("answer-bank-version").textContent = state.data.bank_version ? `Answer bank ${state.data.bank_version}` : "Answer bank version unavailable";
    renderPending();
  }
  function questionCard(q) {
    const card = el("article", null, "question-card"); card.dataset.questionId = q.question_id;
    const occurrences = q.occurrences || [], first = occurrences[0] || {};
    card.append(el("h3", first.question || q.question || "Question text unavailable"), el("code", q.question_id), el("span", q.status === "resolved" || q.status === "answered" ? "Resolved" : "Unresolved", "question-status"));
    const jobs = el("ul", null, "question-jobs");
    for (const occ of occurrences) { const li = el("li"); li.append(el("span", `${occ.company || "Company not recorded"} · ${occ.application_id}`), el("p", occ.question || "Question wording unavailable")); if (occ.options?.length) li.append(el("p", `Options: ${occ.options.map((option) => JSON.stringify(option)).join(" · ")}`)); li.append(el("p", `Context: ${JSON.stringify({ type: occ.type, kind: occ.kind, units: occ.units, scope: occ.scope, resolution: occ.resolution })}`)); li.append(anchor("Open application", appAnchor(occ.application_id))); const saved = safeLink("Saved record ↗", occ.record_path ? `../${occ.record_path}` : null); if (saved) li.append(saved); const src = safeLink("Original listing ↗", occ.listing_url || occ.destination_url || occ.canonical_listing_url); if (src) li.append(src); jobs.append(li); }
    card.append(jobs);
    const form = el("form", null, "answer-form");
    const value = answerInput(first); const source = inputField("Source", "source", "text", "");
    source.input.placeholder = "Describe your confirmation or source"; source.input.required = true;
    const scope = inputField("Scope (JSON)", "scope", "text", JSON.stringify(first.source_scope || first.scope || {}));
    const confirmed = inputField("Confirmed at (ISO 8601 with offset)", "confirmed_at", "text", "");
    confirmed.input.required = true;
    form.append(value.wrap, source.wrap, scope.wrap, confirmed.wrap);
    const submit = el("button", "Add to batch", "primary-button"); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", (event) => { event.preventDefault(); try { if (!state.data.bank_version) throw new Error("Refresh the export to obtain the answer bank version."); const answerValue = readInput(value); const qType = first.type || "text"; const answerId = first.source_answer_id || `answer-${q.question_id}`; const prior = (state.data.answer_sources || []).find((item) => item.id === answerId); const descriptor = { id: answerId, value: answerValue, scope: JSON.parse(scope.input.value), type: qType, aliases: [...(prior?.aliases || []), ...occurrences.map((o) => o.question).filter(Boolean)].filter((a, i, all) => all.findIndex((b) => JSON.stringify(a) === JSON.stringify(b)) === i), basis: "saved_answer", source: source.input.value.trim(), confirmed_at: confirmed.input.value.trim() }; if (prior && Number.isInteger(prior.version)) descriptor.version = prior.version + 1; for (const key of ["kind", "units", "options", "skill"]) if (first[key] !== undefined && first[key] !== null) descriptor[key] = first[key];
      if (!descriptor.source || !descriptor.confirmed_at || !Array.isArray(descriptor.aliases) || !descriptor.aliases.length) throw new Error("Source, confirmation time and exact question wording are required.");
      const timestamp = descriptor.confirmed_at;
      if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(timestamp) || !validDate(timestamp) || Date.parse(timestamp) > Date.now()) throw new Error("Confirmation needs a valid date with UTC offset, no later than now.");
      if (!descriptor.scope || typeof descriptor.scope !== "object" || Array.isArray(descriptor.scope)) throw new Error("Scope must be a JSON object.");
      const existing = state.pending.findIndex((x) => x.answer.id === descriptor.id); const change = { change_id: `change-${q.question_id}-${C.hash(JSON.stringify([state.data.bank_version, descriptor]))}`, answer: descriptor }; if (existing >= 0) state.pending[existing] = change; else state.pending.push(change); setError(""); renderPending(); } catch (error) { setError(`Answer not added: ${error.message}`); } });
    card.append(form);
    return card;
  }
  function inputField(label, name, type, value) { const wrap = el("label", null, "field"); wrap.append(el("span", label)); const input = el("input"); input.name = name; input.type = type; input.value = value || ""; wrap.append(input); return { wrap, input }; }
  function answerInput(occurrence) {
    const row = el("label", null, "field"); row.append(el("span", "Answer"));
    const options = occurrence.options || [], type = occurrence.type || "text";
    let input;
    if (type === "boolean") { input = document.createElement("select"); input.append(new Option("Choose…", ""), new Option("Yes", "true"), new Option("No", "false")); }
    else if (["single_select", "multi_select"].includes(type)) { input = document.createElement("select"); if (type === "multi_select") input.multiple = true; else input.append(new Option("Choose…", "")); for (const option of options) input.append(new Option(JSON.stringify(option), JSON.stringify(option))); }
    else { input = document.createElement("input"); input.type = type === "number" ? "number" : "text"; input.step = "any"; }
    input.dataset.type = type; row.append(input); return { wrap: row, input, occurrence };
  }
  function readInput(item) { const { input } = item, type = input.dataset.type; if (type === "boolean") { if (!input.value) throw new Error("Choose Yes or No."); return input.value === "true"; } if (type === "multi_select") { const values = [...input.selectedOptions].map((o) => JSON.parse(o.value)); if (!values.length) throw new Error("Choose at least one option."); return values; } if (type === "single_select") { if (!input.value) throw new Error("Choose an option."); return JSON.parse(input.value); } if (type === "number") { if (input.value === "") throw new Error("Enter a number."); const number = Number(input.value); if (!Number.isFinite(number)) throw new Error("Enter a finite number."); return number; } if (!input.value.trim()) throw new Error("Enter an answer."); return input.value; }
  function renderPending() {
    const box = $("pending-changes"); box.replaceChildren();
    if (state.pending.length) { box.append(el("p", `${state.pending.length} answer change${state.pending.length === 1 ? "" : "s"} pending export.`)); const exportButton = el("button", "Download answer change file", "primary-button"); exportButton.type = "button"; exportButton.addEventListener("click", () => { const batch = answerChangeFile(); state.lastAnswerImport = { import_id: batch.import_id, changes: JSON.stringify(state.pending) }; try { localStorage.setItem(STORAGE_PREFIX + "pendingAnswerImport", JSON.stringify(state.lastAnswerImport)); } catch {} downloadJson(batch, "answer-changes.json"); }); box.append(exportButton); appendCommand(box, WORKSPACE_CLI + " import-answers --file answer-changes.json"); }
    if (!state.pending.length && state.lastAnswerImport) appendCommand(box, WORKSPACE_CLI + " import-answers --file answer-changes.json");
    const persisted = state.data.answer_receipts || [];
    const matched = persisted.find((r) => r.bank_version === state.data.bank_version && (!state.lastAnswerImport || r.import_id === state.lastAnswerImport.import_id));
    box.append(el("p", matched ? `Persisted import ${matched.import_id} confirmed by refreshed data (${state.data.bank_version}).` : state.pending.length ? "Pending changes are not saved. Import the downloaded file with the CLI, then refresh the dashboard." : state.lastAnswerImport ? "Downloaded answer file is pending import confirmation. Import it with the CLI, then refresh." : "No answer import receipt in the current export."));
  }
  function answerChangeFile() {
    const id = `dashboard-${C.hash(JSON.stringify([state.data.bank_version, state.pending]))}`;
    return { schema_version: 1, import_id: id, base_version: state.data.bank_version, changes: state.pending.map((x) => ({ ...x })) };
  }
  function downloadJson(value, name) { const blob = new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = el("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url); }
  function appendCommand(box, text) {
    const command = el("code", text, "command"); box.append(command);
    const copy = el("button", "Copy command", "small-button"); copy.type = "button";
    copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy command"; } }); box.append(copy);
  }
  function renderSettings() {
    const settings = state.data.settings || {}; const form = $("settings-fields"); form.replaceChildren();
    const editable = Object.keys(settings).filter((key) => key !== "schema_version");
    for (const key of editable) { const label = el("label", null, "field"); label.append(el("span", key)); const input = document.createElement("textarea"); input.value = JSON.stringify(Object.hasOwn(state.settingsChanges, key) ? state.settingsChanges[key] : settings[key], null, 2); input.rows = 2; input.dataset.setting = key; input.addEventListener("input", () => { try { const value = JSON.parse(input.value); if (JSON.stringify(value) === JSON.stringify(settings[key])) delete state.settingsChanges[key]; else state.settingsChanges[key] = value; delete input.dataset.invalid; } catch { input.dataset.invalid = "true"; } renderSettingsStatus(); }); label.append(input); form.append(label); }
    renderSettingsStatus();
  }
  function renderSettingsStatus() {
    const box = $("settings-status"); box.replaceChildren(); const keys = Object.keys(state.settingsChanges);
    box.append(el("p", keys.length ? `${keys.length} setting change${keys.length === 1 ? "" : "s"} pending export. Not saved until imported and refreshed.` : "Settings shown from the latest export."));
    if (keys.length) { const download = el("button", "Download settings change file", "primary-button"); download.type = "button"; download.addEventListener("click", () => { if (!state.data.settings_hash) { setError("Refresh the export to obtain the settings hash."); return; } if (document.querySelector('[data-invalid="true"]')) { setError("Fix invalid JSON setting values before export."); return; } state.lastSettingsDesired = { ...state.settingsChanges }; try { localStorage.setItem(STORAGE_PREFIX + "pendingSettingsDesired", JSON.stringify(state.lastSettingsDesired)); } catch {} downloadJson({ schema_version: 1, base_hash: state.data.settings_hash, changes: state.settingsChanges }, "settings-changes.json"); }); box.append(download);
      appendCommand(box, WORKSPACE_CLI + " settings-import --file settings-changes.json"); }

    if (!keys.length && state.lastSettingsDesired) appendCommand(box, WORKSPACE_CLI + " settings-import --file settings-changes.json");
    const confirmed = state.lastSettingsDesired && Object.entries(state.lastSettingsDesired).every(([key, value]) => JSON.stringify(state.data.settings?.[key]) === JSON.stringify(value)); box.append(el("p", confirmed ? "Settings import confirmed by refreshed data." : keys.length || state.lastSettingsDesired ? "A download is pending; this page does not write settings." : "Settings are read from the current export."));
  }
  function renderAll() {
    renderStats(); renderAttention(); renderApplications(); renderRuns(); renderQuestions(); renderSettings();
    $("updated").textContent = state.data.generated_at ? `Updated ${dateText(state.data.generated_at)} · ${TZ}` : "Load application JSON to get started";
    setError(state.skipped ? `Skipped ${state.skipped} malformed record${state.skipped === 1 ? "" : "s"}; valid applications are shown.` : "");
  }
  function setupFilters() {
    const keys = ["status", "company", "channel", "from", "to", "applied_by", "stop_reason"];
    for (const key of keys) { const input = $(`filter-${key}`); input.value = state.filters[key] || ""; input.addEventListener(key === "status" ? "change" : "input", () => { state.filters[key] = input.value; syncHash(); renderApplications(); renderRuns(); }); }

  }
  function readHash() { const raw = location.hash.startsWith("#?") ? location.hash.slice(2) : ""; const p = new URLSearchParams(raw); state.filters = Object.fromEntries(["status", "company", "channel", "from", "to", "applied_by", "stop_reason"].map((k) => [k, p.get(k) || ""]).filter(([,v]) => v)); for (const key of ["status", "company", "channel", "from", "to", "applied_by", "stop_reason"]) { const input = $(`filter-${key}`); if (input) input.value = state.filters[key] || ""; } }
  function syncHash() { const p = new URLSearchParams(Object.entries(state.filters).filter(([,v]) => v)); history.replaceState(null, "", p.size ? `#?${p}` : location.pathname + location.search); }
  function setupTabs() { document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => { state.tab = button.dataset.tab; document.querySelectorAll("[data-view]").forEach((section) => { section.hidden = section.dataset.view !== state.tab; }); document.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b === button))); })); }
  function openApplicationHash() { if (!location.hash.startsWith("#application-")) return; let id; try { id = decodeURIComponent(location.hash.slice("#application-".length)); } catch { return; } const button = document.querySelector('[data-tab="applications"]'); if (button) button.click(); const app = allRecords().find((item) => item.id === id); if (app) showDetails(app); }
  document.querySelectorAll("th[data-sort]").forEach((th) => th.querySelector("button").addEventListener("click", () => { const key = th.dataset.sort; if (state.sort.key === key) state.sort.direction = state.sort.direction === "asc" ? "desc" : "asc"; else { state.sort.key = key; state.sort.direction = "asc"; } renderApplications(); }));
  $("refresh").addEventListener("click", () => location.reload()); $("close-details").addEventListener("click", () => $("details").close());
  $("import").addEventListener("change", async (event) => { try { const ps = await Promise.all([...event.target.files].map(async (f) => JSON.parse(await f.text()))); loadData(window.JobRuns.mergePayloads(ps)); } catch (e) { setError(`Could not load JSON: ${e.message}`); } finally { event.target.value = ""; } });
  setupTabs(); readHash(); setupFilters(); window.addEventListener("hashchange", () => { if (location.hash.startsWith("#application-")) openApplicationHash(); else { readHash(); renderStatusOptions(); renderApplications(); renderRuns(); } });
  window.JobJournal = { loadData, state };
  try { loadData(window.JOB_SEARCH_DATA || { applications: [] }); openApplicationHash(); } catch (e) { setError(e.message); }
})();
