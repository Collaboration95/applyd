const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync(require.resolve('../dashboard/control.js'), 'utf8'), { window, Intl, Date, URL, String, Set, Math });
const C = window.DashboardControl;
const fixture = [
  { id: 'a1', company: 'Synthetic One', title: 'Engineer', status: 'submitted', job_url: 'https://jobs.example/a', applied_by: 'Agent A', submissions: [{ at: '2026-10-20T02:00:00Z' }] },
  { id: 'a2', company: 'Synthetic Two', title: 'Analyst', status: 'awaiting_answer', stop_reason: 'missing qualification', blocked_on_answer_ids: ['q-1'], job_url: 'https://www.linkedin.com/jobs/view/2' },
  { id: 'a3', company: 'Synthetic Three', title: 'Intern', status: 'submission_uncertain', job_url: 'https://jobs.example/c' },
  { id: 'a4', company: 'Synthetic Four', title: 'Developer', status: 'failed', stop_reason: 'synthetic error' },
  { id: 'a5', company: 'Synthetic Five', title: 'Trainee', status: 'preparing', updated_at: '2026-10-18T00:00:00Z' },
  { id: 'a6', company: 'Synthetic Six', title: 'Designer', status: 'ready', updated_at: '2026-10-20T00:00:00Z' }
];

// A deliberately small DOM, built from the real HTML. Absent IDs return null;
// append converts non-nodes to text, matching the browser failure in round one.
class Node {
  constructor(tag = '#text', text = '') { this.tagName = tag.toLowerCase(); this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this._text = text; this._value = ''; this.hidden = false; this.selected = false; this.multiple = false; }
  setAttribute(name, value = '') { this.attributes[name] = String(value); if (name === 'id') this.id = value; if (name === 'class') this.className = value; if (name === 'value') this.value = value; if (name === 'hidden') this.hidden = true; if (name === 'name') this.name = value; if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  append(...items) { for (const item of items) { const child = item instanceof Node ? item : new Node('#text', String(item)); child.parent = this; this.children.push(child); } }
  replaceChildren(...items) { this.children = []; this._text = ''; this.append(...items); }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  get options() { return this.children.filter((n) => n.tagName === 'option'); }
  get value() { if (this.tagName === 'select') return this.options.find((o) => o.selected)?.value ?? this.options[0]?.value ?? ''; return this._value; }
  set value(value) { this._value = String(value); if (this.tagName === 'select') this.options.forEach((o) => { o.selected = o.value === String(value); }); }
  get selectedOptions() { return this.options.filter((o) => o.selected); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  async dispatch(type) { for (const cb of this.listeners[type] || []) await cb({ target: this, preventDefault() {} }); }
  click() { return this.dispatch('click'); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  matches(selector) {
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    if (selector.startsWith('.')) return (this.className || '').split(' ').includes(selector.slice(1));
    const tag = selector.match(/^[\w-]+/); if (tag && this.tagName !== tag[0]) return false;
    const attrs = [...selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return (tag || attrs.length) && attrs.every(([, key, value]) => {
      const actual = key.startsWith('data-') ? this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] : this.attributes[key] ?? this[key];
      return value === undefined ? actual !== undefined : actual === value;
    });
  }
  querySelectorAll(selector) { return this.children.flatMap((c) => [...(c.matches(selector) ? [c] : []), ...c.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function harness(payload = { applications: [] }, storage = new Map(), hash = '', baseURI = 'file:///synthetic/dashboard/index.html') {
  const html = fs.readFileSync(require.resolve('../dashboard/index.html'), 'utf8');
  const doc = new Node('document'), stack = [doc];
  for (const token of html.match(/<[^>]+>|[^<]+/g)) {
    if (token.startsWith('</')) { stack.pop(); continue; }
    if (token.startsWith('<!')) continue;
    if (!token.startsWith('<')) { stack.at(-1).append(new Node('#text', token)); continue; }
    const tag = token.match(/^<([\w-]+)/)[1], node = new Node(tag);
    for (const [, key, value, bare] of token.matchAll(/([\w-]+)(?:="([^"]*)"|='([^']*)')?/g)) {
      if (key !== tag) node.setAttribute(key, value ?? bare ?? '');
    }
    stack.at(-1).append(node);
    if (!['meta', 'link', 'input', 'br', 'hr', 'img'].includes(tag)) stack.push(node);
  }
  doc.getElementById = (id) => doc.querySelector(`#${id}`);
  doc.createElement = (tag) => new Node(tag);
  doc.baseURI = baseURI;
  const downloads = [], copied = [], listeners = {};
  const location = { protocol: 'file:', pathname: '/synthetic/dashboard/index.html', search: '', hash, reload() {} };
  const history = { replaceState(_state, _title, url) { location.hash = url.includes('#') ? url.slice(url.indexOf('#')) : ''; } };
  class LocalURL extends URL {}
  LocalURL.createObjectURL = (blob) => { downloads.push(blob); return 'blob:synthetic'; };
  LocalURL.revokeObjectURL = () => {};
  function Option(text, value) { const option = new Node('option', text); option.value = value; return option; }
  const win = { JOB_SEARCH_DATA: payload, addEventListener(type, callback) { listeners[type] = callback; } };
  const context = vm.createContext({ window: win, document: doc, location, history, localStorage: { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v) }, navigator: { clipboard: { async writeText(v) { copied.push(v); } } }, Option, URL: LocalURL, URLSearchParams, Blob, Date, Intl });
  for (const name of ['runs.js', 'control.js', 'app.js']) vm.runInContext(fs.readFileSync(require.resolve(`../dashboard/${name}`), 'utf8'), context, { filename: name });
  return { window: win, document: doc, get: (id) => doc.getElementById(id), downloads, copied, storage, location, listeners };
}
async function makeBatch(payload, values) {
  const h = harness(payload);
  assert.equal(h.get('error').hidden, true, h.get('error').textContent);
  for (const [i, form] of h.get('questions-list').querySelectorAll('form').entries()) {
    const value = values[i], input = form.querySelectorAll('label')[0].querySelector('input') || form.querySelector('select');
    if (input.multiple) input.options.forEach((o) => { o.selected = value.some((v) => JSON.stringify(v) === o.value); });
    else input.value = typeof value === 'string' && !['single_select', 'boolean'].includes(input.dataset.type) ? value : JSON.stringify(value);
    form.querySelector('[name="source"]').value = 'Synthetic user confirmation';
    form.querySelector('[name="confirmed_at"]').value = '2026-10-20T10:00:00+08:00';
    await form.dispatch('submit');
    assert.equal(h.get('error').hidden, true, h.get('error').textContent);
  }
  await h.get('pending-changes').querySelector('button').click();
  return JSON.parse(await h.downloads.at(-1).text());
}
module.exports = { harness, makeBatch };
if (require.main === module && process.argv.includes('--batch')) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  makeBatch(input.payload, input.values).then((batch) => process.stdout.write(JSON.stringify(batch))).catch((err) => { console.error(err); process.exitCode = 1; });
} else if (require.main === module) {
test('stat counts use records and submitted timestamps in the exported timezone', () => {
  assert.deepEqual({ ...C.stats(fixture, new Date('2026-10-20T08:00:00Z'), 'Asia/Singapore') }, {
    Applied: 1, 'Awaiting answer': 1, Preparing: 2, Failed: 1, Skipped: 0, Today: 1, '7 days': 1
  });
});
test('attention includes every nonterminal record with stale and fresh reasons', () => {
  const attention = C.attention(fixture, 'UTC', new Date('2026-10-20T12:00:00Z'));
  assert.deepEqual(attention.map((x) => x.id), ['a2', 'a3', 'a4', 'a5', 'a6']);
  assert.match(attention[0].reason, /qualification/);
  assert.equal(attention[0].listing_url, fixture[1].job_url);
  assert.match(attention[3].reason, /24 hours/);
  assert.equal(attention[3].stale, true);
  assert.equal(attention[4].stale, false);
  assert.match(attention[4].reason, /queued/);
  assert.equal(C.attention([{ id: 'found', status: 'discovered' }])[0].reason, 'Queued for preparation.');
});
test('filters cover employer, channel host, date, agent and stop reason', () => {
  const selected = C.filterApplications(fixture, { company: 'one', channel: 'example', from: '2026-10-20', to: '2026-10-20', applied_by: 'agent', stop_reason: '' }, 'UTC');
  assert.deepEqual(selected.map((x) => x.id), ['a1']);
  assert.equal(C.filterApplications(fixture, { stop_reason: 'qualification' }, 'UTC').map((x) => x.id).join(','), 'a2');
});
test('funnel separates ready, submitted and responses', () => {
  assert.deepEqual({ ...C.funnel(fixture, [{ application_id: 'a1' }]) }, { Captured: 6, Ready: 2, Submitted: 1, Responded: 1 });
});

const payload = (extra = {}) => ({ applications: fixture, timezone: 'Asia/Singapore', bank_version: 'bank-v1', settings_hash: 'sha256-exported-hash', settings: { schema_version: 1, mode: 'dry_run' }, ...extra });
const question = (type = 'text', options = []) => ({ question_id: 'q-stable', status: 'unresolved', occurrences: [{ application_id: 'a2', company: 'Synthetic Two', question: 'Exact wording?', type, options, scope: {}, kind: 'fact', occurrence_id: 'occ-stable' }] });
test('complete app boots with actual HTML IDs, renders and clears a malformed banner on clean reload', () => {
  const h = harness(payload());
  assert.ok(h.window.JobJournal, 'startup must finish');
  assert.equal(h.get('search'), null);
  assert.equal(h.get('settings-download'), null);
  assert.equal(h.get('error').hidden, true, h.get('error').textContent);
  assert.equal(h.get('stats').children.length, 7);
  assert.equal(h.get('application-rows').children.length, 6);
  h.window.JobJournal.loadData(payload({ applications: [null, ...fixture] }));
  assert.match(h.get('error').textContent, /Skipped 1/);
  h.window.JobJournal.loadData(payload());
  assert.equal(h.get('error').hidden, true);
  h.window.JobJournal.loadData(payload({ applications: [] }));
  assert.equal(h.get('application-empty').hidden, false);
});
test('current-only status dropdown and hash filters cover Applications and Runs', async () => {
  const historyApp = { ...fixture[0], status: 'interview' };
  const h = harness(payload({ applications: [historyApp, fixture[1]] }));
  assert.deepEqual(h.get('filter-status').options.map((o) => o.value), ['', 'awaiting_answer', 'interview']);
  h.get('filter-status').value = 'interview'; await h.get('filter-status').dispatch('change');
  assert.equal(h.get('application-rows').children.length, 1);
  assert.equal(h.get('runs').querySelectorAll('li').length, 1);
  assert.match(h.location.hash, /status=interview/);
  h.location.hash = '#?status=awaiting_answer&company=Two'; h.listeners.hashchange();
  assert.equal(h.get('application-rows').children[0].dataset.applicationId, 'a2');
  h.location.hash = ''; h.listeners.hashchange();
  assert.equal(h.get('filter-company').value, '');
  assert.equal(h.get('application-rows').children.length, 2);
});
test('sort headers contain native keyboard buttons with direction and response sorting', async () => {
  const h = harness(payload({ applications: [{ ...fixture[0], response: 'Zebra' }, { ...fixture[1], response: 'Alpha' }] }));
  const th = h.document.querySelector('th[data-sort="response"]');
  assert.equal(th.querySelector('button').tagName, 'button');
  await th.querySelector('button').click();
  assert.equal(th.getAttribute('aria-sort'), 'ascending');
  assert.equal(h.get('application-rows').children[0].dataset.applicationId, 'a2');
  await th.querySelector('button').click();
  assert.equal(th.getAttribute('aria-sort'), 'descending');
});
test('question form renders real answer control, exact options/context, and requires real provenance', async () => {
  const h = harness(payload({ inbox: { questions: [question('single_select', [true, 1, '1'])] } }));
  const card = h.get('questions-list').children[0], form = card.querySelector('form');
  assert.ok(form.querySelector('select'));
  assert.doesNotMatch(form.textContent, /\[object Object\]/);
  assert.match(card.textContent, /Context:/);
  assert.equal(form.querySelector('[name="source"]').value, '');
  form.querySelector('select').value = 'true';
  form.querySelector('[name="confirmed_at"]').value = '2026-10-20T10:00:00+08:00';
  await form.dispatch('submit');
  assert.match(h.get('error').textContent, /Source/);
  assert.equal(h.window.JobJournal.state.pending.length, 0);
});
test('answer batch preserves JSON scalar types and explicit user-review basis', async () => {
  const questions = [question('single_select', [true, 1, '1']), { ...question('multi_select', [false, 2, '2']), question_id: 'q-second', occurrences: [{ ...question('multi_select', [false, 2, '2']).occurrences[0], question: 'Second exact wording?', basis: 'authorized_default' }] }];
  const batch = await makeBatch(payload({ inbox: { questions } }), [true, [false, 2, '2']]);
  assert.equal(batch.changes[0].answer.value, true);
  assert.deepEqual(batch.changes[1].answer.value, [false, 2, '2']);
  assert.equal(batch.changes[1].answer.basis, 'saved_answer');
  assert.equal(batch.base_version, 'bank-v1');
});
test('revising an answer uses distinct change/import IDs and replaces the current source', async () => {
  const first = await makeBatch(payload({ inbox: { questions: [question()] } }), ['One']);
  const source = { ...first.changes[0].answer, version: 4 };
  const q = question(); q.occurrences[0].source_answer_id = source.id;
  const second = await makeBatch(payload({ bank_version: 'bank-v2', answer_sources: [source], inbox: { questions: [q] } }), ['Two']);
  assert.equal(second.changes[0].answer.id, source.id);
  assert.equal(second.changes[0].answer.version, 5);
  assert.notEqual(second.changes[0].change_id, first.changes[0].change_id);
  assert.notEqual(second.import_id, first.import_id);
});
test('separate private dashboard URLs do not share pending imports', () => {
  const storage = new Map([['applyd:file:///synthetic/dashboard/index.html:pendingAnswerImport', JSON.stringify({import_id:'synthetic-pending'})]]);
  const first = harness(payload(), storage);
  assert.equal(first.window.JobJournal.state.lastAnswerImport.import_id, 'synthetic-pending');
  const second = harness(payload(), storage, '', 'file:///another-synthetic/dashboard/index.html');
  assert.equal(second.window.JobJournal.state.lastAnswerImport, null);
});
test('answer download is pending until receipt refresh and exact CLI command is copyable', async () => {
  const h = harness(payload({ inbox: { questions: [question()] } }));
  const form = h.get('questions-list').querySelector('form');
  form.querySelector('input').value = 'Confirmed';
  form.querySelector('[name="source"]').value = 'Synthetic review';
  form.querySelector('[name="confirmed_at"]').value = '2026-10-20T10:00:00+08:00';
  await form.dispatch('submit');
  await h.get('pending-changes').querySelector('button').click();
  assert.match(h.get('pending-changes').textContent, /not saved/);
  const buttons = h.get('pending-changes').querySelectorAll('button'); await buttons.at(-1).click();
  assert.equal(h.copied[0], 'python3 scripts/workspace.py --workspace "${APPLYD_WORKSPACE:?Set APPLYD_WORKSPACE for this export}" import-answers --file answer-changes.json');
  const batch = JSON.parse(await h.downloads[0].text());
  const reloaded = harness(payload(), h.storage);
  assert.match(reloaded.get('pending-changes').textContent, /pending import confirmation/);
  h.window.JobJournal.loadData(payload({ bank_version: 'bank-v2', answer_receipts: [{ import_id: batch.import_id, bank_version: 'bank-v2' }] }));
  assert.equal(h.window.JobJournal.state.pending.length, 0);
  assert.match(h.get('pending-changes').textContent, /Persisted import/);
});
test('settings file uses exported SHA256 hash and only reports persistence after refresh', async () => {
  const h = harness(payload()), field = h.get('settings-fields').querySelector('textarea');
  field.value = '"save_only"'; await field.dispatch('input');
  await h.get('settings-status').querySelector('button').click();
  const change = JSON.parse(await h.downloads.at(-1).text());
  assert.deepEqual(change, { schema_version: 1, base_hash: 'sha256-exported-hash', changes: { mode: 'save_only' } });
  assert.doesNotMatch(h.get('settings-status').textContent, /import confirmed/);
  await h.get('settings-status').querySelectorAll('button').at(-1).click();
  assert.equal(h.copied.at(-1), 'python3 scripts/workspace.py --workspace "${APPLYD_WORKSPACE:?Set APPLYD_WORKSPACE for this export}" settings-import --file settings-changes.json');
  h.window.JobJournal.loadData(payload({ settings_hash: 'new-exported-hash', settings: { schema_version: 1, mode: 'save_only' } }));
  assert.match(h.get('settings-status').textContent, /import confirmed/);
});
test('invalid settings JSON cannot download a previous valid edit', async () => {
  const h = harness(payload()), field = h.get('settings-fields').querySelector('textarea');
  field.value = '"save_only"'; await field.dispatch('input');
  field.value = '{'; await field.dispatch('input');
  await h.get('settings-status').querySelector('button').click();
  assert.equal(h.downloads.length, 0);
  assert.match(h.get('error').textContent, /invalid JSON/);
});
test('application deep link opens details even with an excluding filter', () => {
  const h = harness(payload(), new Map(), '#?company=absent');
  h.location.hash = '#application-a2'; h.listeners.hashchange();
  assert.equal(h.get('details').open, true);
  assert.equal(h.get('detail-title').textContent, 'Analyst');
});

test('channel/ATS host filter searches destination even when a channel is explicitly named', () => {
  const app = { ...fixture[0], channel: 'External', application_url: 'https://synthetic.myworkdayjobs.com/job/1' };
  assert.equal(C.filterApplications([app], { channel: 'myworkdayjobs' }).length, 1);
  assert.equal(C.filterApplications([app], { channel: 'External' }).length, 1);
});
test('loading JSON through the actual file control renders and clears old malformed warnings', async () => {
  const h = harness(payload({ applications: [null, ...fixture] }));
  h.get('import').files = [{ async text() { return JSON.stringify(payload()); } }];
  await h.get('import').dispatch('change');
  assert.equal(h.get('error').hidden, true);
  assert.equal(h.get('application-rows').children.length, 6);
});
test('a receipt for an earlier download never clears a newer staged answer', async () => {
  const h = harness(payload({ inbox: { questions: [question()] } }));
  const form = h.get('questions-list').querySelector('form');
  form.querySelector('input').value = 'First';
  form.querySelector('[name="source"]').value = 'Synthetic review';
  form.querySelector('[name="confirmed_at"]').value = '2026-10-20T10:00:00+08:00';
  await form.dispatch('submit');
  await h.get('pending-changes').querySelector('button').click();
  const first = JSON.parse(await h.downloads[0].text());
  form.querySelector('input').value = 'Newer'; await form.dispatch('submit');
  h.window.JobJournal.loadData(payload({ bank_version: 'new-bank', answer_receipts: [{ import_id: first.import_id, bank_version: 'new-bank' }] }));
  assert.equal(h.window.JobJournal.state.pending.length, 1);
  assert.equal(h.window.JobJournal.state.pending[0].answer.value, 'Newer');
});
test('missing exported bases prevent producing unimportable change files', async () => {
  const h = harness(payload({ settings_hash: null }));
  const field = h.get('settings-fields').querySelector('textarea');field.value='"save_only"';await field.dispatch('input');
  await h.get('settings-status').querySelector('button').click();
  assert.equal(h.downloads.length, 0);
  assert.match(h.get('error').textContent, /settings hash/);
});

test('attention link can reopen a closed application with the same hash', async () => {
  const h = harness(payload());
  const link = h.get('attention-items').querySelector('a');
  await link.click(); assert.equal(h.get('details').open, true);
  await h.get('close-details').click(); assert.equal(h.get('details').open, false);
  await link.click(); assert.equal(h.get('details').open, true);
});

}
