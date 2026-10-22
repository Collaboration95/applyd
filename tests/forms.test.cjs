'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { File } = require('node:buffer');
const { createHash, webcrypto } = require('node:crypto');
const forms = require('../scripts/form_handlers.js');
const source = fs.readFileSync(path.join(__dirname, '../scripts/form_handlers.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'fixtures/application-form.html'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const bytes = Buffer.from('%PDF-1.4\nsynthetic form test only\n%%EOF\n');
const resume = { id: 'synthetic-release', path: '/synthetic/resume.pdf', sha256: createHash('sha256').update(bytes).digest('hex') };
const values = { 'full-name': 'Example Applicant', 'years-python': 1, 'work-category': 'Foreigner',
  sponsorship: 'Yes', 'background-check': true, 'history-company': 'Example Cedar Labs',
  'history-start': '2025-01', 'history-end': '2025-12' };
const guard = () => true;

// Deliberately small DOM double. Parses the checked-in HTML; models only native
// properties/selectors needed by this adapter. It is NOT browser verification.
class Element {
  constructor(tagName, attrs, parent, doc) {
    this.tagName = tagName.toUpperCase(); this.attrs = attrs; this.parentElement = parent;
    this.ownerDocument = doc; this.nodeType = 1; this.children = []; this.files = [];
    this._value = attrs.value ?? ''; this._checked = Object.hasOwn(attrs, 'checked');
  }
  getAttribute(key) { return this.attrs[key] ?? null; }
  get id() { return this.attrs.id || ''; }
  get name() { return this.attrs.name || ''; }
  get type() { return this.attrs.type || 'text'; }
  get hidden() { return Object.hasOwn(this.attrs, 'hidden'); }
  get inert() { return Object.hasOwn(this.attrs, 'inert'); }
  get isConnected() { return this.ownerDocument.contains(this); }
  get required() { return Object.hasOwn(this.attrs, 'required'); }
  get readOnly() { return Object.hasOwn(this.attrs, 'readonly'); }
  get disabled() { return Object.hasOwn(this.attrs, 'disabled'); }
  get multiple() { return Object.hasOwn(this.attrs, 'multiple'); }
  get size() { return Number(this.attrs.size || 0); }
  get min() { return this.attrs.min || ''; }
  get max() { return this.attrs.max || ''; }
  get step() { return this.attrs.step || ''; }
  get pattern() { return this.attrs.pattern || ''; }
  get accept() { return this.attrs.accept || ''; }
  get maxLength() { return Object.hasOwn(this.attrs, 'maxlength') ? Number(this.attrs.maxlength) : -1; }
  get textContent() { return this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  get labels() { return this.ownerDocument.querySelectorAll('label').filter(label => label.getAttribute('for') === this.id); }
  get options() { return this.querySelectorAll('option'); }
  get label() { return this.attrs.label ?? this.textContent.trim(); }
  get value() { return this.attrs.value ?? this.textContent.trim(); }
  get valueAsNumber() { return this.value === '' ? NaN : Number(this.value); }
  get validity() {
    const empty = this.type === 'file' ? !this.files.length : this.type === 'checkbox' ? !this.checked : !this.value;
    return { valid: !this.required || !empty || this.type === 'radio' };
  }
  getClientRects() { return [{}]; }
  contains(other) { return this === other || this.children.some(child => typeof child !== 'string' && child.contains(other)); }
  matches(selector) {
    if (selector === ':disabled') return this.disabled || Boolean(this.parentElement?.closest('fieldset')?.disabled);
    if (selector === '[role="dialog"], dialog') return this.tagName === 'DIALOG' || this.attrs.role === 'dialog';
    if (selector === 'fieldset') return this.tagName === 'FIELDSET';
    if (/^[a-z]+$/.test(selector)) return this.tagName === selector.toUpperCase();
    if (selector === '[aria-required="true"]') return this.attrs['aria-required'] === 'true';
    // The adapter's one custom-control selector list.
    if (selector.startsWith('[contenteditable]')) return (Object.hasOwn(this.attrs, 'contenteditable') && this.attrs.contenteditable !== 'false') ||
      ['combobox', 'listbox', 'slider', 'switch', 'checkbox', 'radio', 'textbox', 'spinbutton'].includes(this.attrs.role);
    throw new Error(`unmodelled selector: ${selector}`);
  }
  closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
  querySelectorAll(selector) {
    const found = [];
    const walk = node => {
      for (const child of node.children) if (typeof child !== 'string') {
        const matches = selector === 'input, textarea, select' ? ['INPUT', 'TEXTAREA', 'SELECT'].includes(child.tagName) : child.matches(selector);
        if (matches) found.push(child);
        walk(child);
      }
    };
    walk(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  dispatchEvent(event) { this.ownerDocument.onEvent?.(this, event); return true; }
}
class Input extends Element {
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  get checked() { return this._checked; }
  set checked(value) {
    if (this.type === 'radio' && value) for (const node of this.ownerDocument.querySelectorAll('input')) {
      if (node !== this && node.name === this.name) node._checked = false;
    }
    this._checked = Boolean(value);
  }
}
class Textarea extends Element {
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
}
class Select extends Element {
  get value() { return this._value || this.options[0]?.value || ''; }
  set value(value) { this._value = this.options.some(option => option.value === String(value)) ? String(value) : ''; }
}
function parse(markup = html) {
  const doc = new Element('document', {}, null, null); doc.ownerDocument = doc;
  doc.location = { href: 'file:///synthetic/application-form.html' };
  doc.defaultView = { crypto: webcrypto, HTMLInputElement: Input, HTMLTextAreaElement: Textarea,
    HTMLSelectElement: Select, Event: class { constructor(type) { this.type = type; } },
    getComputedStyle: node => ({ display: node.attrs.style?.includes('display:none') ? 'none' : 'block', visibility: 'visible' }) };
  doc.getElementById = id => {
    const walk = node => node.id === id ? node : node.children.filter(child => typeof child !== 'string').map(walk).find(Boolean);
    return walk(doc) || null;
  };
  const stack = [doc];
  const body = markup.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '');
  for (const match of body.matchAll(/<![^>]*>|<\/[\w-]+\s*>|<[\w-]+\b[^>]*>|[^<]+/g)) {
    const token = match[0];
    if (token.startsWith('<!')) continue;
    if (token.startsWith('</')) { stack.pop(); continue; }
    if (!token.startsWith('<')) { stack.at(-1).children.push(token); continue; }
    const tag = token.match(/^<([\w-]+)/)[1], attrs = {};
    for (const attr of token.slice(tag.length + 1, -1).matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) attrs[attr[1]] = attr[2] ?? attr[3] ?? attr[4] ?? '';
    const Type = { input: Input, textarea: Textarea, select: Select }[tag] || Element;
    const node = new Type(tag, attrs, stack.at(-1), doc); stack.at(-1).children.push(node);
    if (!['input', 'meta', 'link', 'br', 'hr'].includes(tag)) stack.push(node);
  }
  return doc;
}
function adapterFor(doc, upload) {
  return forms.createDOMAdapter({ getRoot: () => doc.getElementById('application-dialog'),
    getScope: () => ({ application_id: 'app-synthetic', tab_id: 'tab-synthetic', url: doc.location.href, kind: 'synthetic_application' }), upload });
}
function inputFor(observation) {
  const answers = [], bindings = [], saved_history = {};
  for (const field of observation.fields) {
    if (field.type === 'file') { bindings.push({ field_id: field.field_id }); continue; }
    const answer = { application_id: observation.scope.application_id, occurrence_id: `occ-${field.field_id}`,
      field_id: field.field_id, question: field.label, type: { select: 'single_select', radio: 'single_select', checkbox: 'boolean' }[field.type] || field.type,
      options: field.options.map(option => option.label), required: field.required, resolution: 'resolved', answer: values[field.field_id] };
    answers.push(answer);
    const binding = { field_id: field.field_id, occurrence_id: answer.occurrence_id };
    if (field.section === 'Work history') {
      binding.history_key = field.field_id;
      saved_history[field.field_id] = answer.answer;
    }
    bindings.push(binding);
  }
  return { observation, approved: { application_id: observation.scope.application_id, snapshot_id: 'synthetic-snapshot', answers, saved_history }, bindings, resume };
}
async function scenario() {
  const doc = parse();
  const adapter = adapterFor(doc, async ({ input }) => { input.files = [new File([bytes], 'synthetic.pdf')]; return clone(resume); });
  const input = inputFor(await adapter.observe());
  return { doc, adapter, input };
}
function memory(input) {
  const state = clone(input.observation), writes = [];
  const adapter = {
    canUpload: true,
    async observe(...args) { assert.equal(args.length, 0, 'reader must not receive expected answers'); return clone(state); },
    async write(action, scope) {
      assert.deepEqual(scope, state.scope);
      writes.push(action.field_id);
      const field = state.fields.find(item => item.field_id === action.field_id);
      field.value = action.kind === 'upload_resume' ? clone(action.resume) : action.value;
      field.valid = true;
    }
  };
  return { state, writes, adapter };
}
const options = { guard, resumeResolver: () => clone(resume) };

test('plan is deterministic, immutable, occurrence-bound and never writes', async () => {
  const { input } = await scenario();
  const before = clone(input), first = forms.plan(input);
  assert.equal(first.status, 'planned'); assert.deepEqual(first, forms.plan(input)); assert.deepEqual(input, before);
  assert.equal(Object.isFrozen(first.actions[0]), true);
  assert.throws(() => { first.actions[0].value = 'tamper'; }, TypeError);
  assert.equal((await forms.execute(clone(first), memory(input).adapter, options)).reason, 'invalid_plan');
});

test('text, number, select, radio, checkbox, upload and exact history verify from independent observations', async () => {
  const { input } = await scenario(), { adapter, writes, state } = memory(input);
  const result = await forms.execute(forms.plan(input), adapter, options);
  assert.equal(result.status, 'verified'); assert.equal(writes.length, 9);
  assert.equal(state.fields.find(field => field.field_id === 'work-category').value, 'foreigner');
  assert.equal(state.fields.find(field => field.field_id === 'sponsorship').value, 'yes');
  assert.equal(result.observed.fields.find(field => field.field_id === 'history-start').value, '2025-01');
});

test('reject unknown required field, changed option wording, duplicate options and controls before writing', async () => {
  const { input } = await scenario();
  const changes = [
    value => value.observation.fields.push({ ...clone(value.observation.fields[0]), field_id: 'new', label: 'New material fact' }),
    value => { value.observation.fields[2].options[0].label = 'New option'; },
    value => { value.observation.fields[2].options[1].value = 'citizen'; },
    value => { value.observation.fields[2].options[1].label = ' CITIZEN '; },
    value => value.observation.fields.push(clone(value.observation.fields[0])),
    value => { value.observation.fields[5].label = 'Passport'; },
    value => { value.observation.fields[0].type = 'custom_widget'; }
  ];
  for (const change of changes) {
    const next = clone(input); change(next);
    const result = forms.plan(next); assert.equal(result.reason, 'unsupported_form');
    const fake = memory(input); await forms.execute(result, fake.adapter, options); assert.deepEqual(fake.writes, []);
  }
});

test('reject mismatched occurrence, application, question, type, required flag, field ID and omitted approved row', async () => {
  const { input } = await scenario();
  const changes = [
    value => { value.bindings[0].occurrence_id = 'other'; },
    value => { value.approved.answers[0].application_id = 'other'; },
    value => { value.approved.answers[0].question = 'Other question'; },
    value => { value.approved.answers[0].type = 'number'; },
    value => { value.approved.answers[0].required = false; },
    value => { value.approved.answers[0].field_id = 'other'; },
    value => { value.bindings.shift(); },
    value => { value.approved.answers[0].resolution = 'unresolved'; },
    value => { value.approved.answers.push(clone(value.approved.answers[0])); }
  ];
  for (const change of changes) { const next = clone(input); change(next); assert.equal(forms.plan(next).status, 'handoff'); }
});

test('ambiguous repeated wording needs distinct prepared field IDs', async () => {
  const { input } = await scenario();
  input.observation.fields[6].label = input.observation.fields[0].label;
  input.approved.answers[5].question = input.approved.answers[0].question;
  assert.equal(forms.plan(input).status, 'planned');
  delete input.approved.answers[0].field_id;
  assert.equal(forms.plan(input).reason, 'unsupported_form');
});

test('history is exact provided saved data; missing/changed facts never infer dates', async () => {
  const { input } = await scenario();
  input.approved.saved_history['history-start'] = '2025-02';
  assert.equal(forms.plan(input).reason, 'history_mismatch');
  delete input.approved.saved_history;
  assert.equal(forms.plan(input).reason, 'history_mismatch');
});

test('no profile changes or unsupported scope, including allowlisted LinkedIn ATS disguise', async () => {
  const { input } = await scenario();
  for (const kind of ['profile', 'other']) {
    const next = clone(input); next.observation.scope.kind = kind;
    assert.equal(forms.plan(next).reason, 'unsupported_form');
  }
  input.observation.fields[0].label = input.approved.answers[0].question = 'Save answers to profile';
  assert.equal(forms.plan(input).reason, 'unsupported_form');
  input.observation.scope.kind = 'ats_application';
  input.observation.scope.url = 'https://www.linkedin.com/in/synthetic/';
  assert.equal(forms.plan(input).status, 'handoff');
});

test('ATS fallback is a review hint only; Easy Apply always hands off to agent', async () => {
  const { input } = await scenario();
  input.observation.fields[0].type = 'custom_widget';
  input.observation.scope.kind = 'easy_apply'; input.observation.scope.url = 'https://www.linkedin.com/jobs/view/900001';
  assert.equal(forms.plan(input).next, 'agent_review');
  input.observation.scope.kind = 'ats_application'; input.observation.scope.url = 'https://jobs.example.test/application';
  assert.equal(forms.plan(input).next, 'agent_review_allowed_ats_fallback');
});

test('layout, options, dialog, tab and URL change after planning stop before writes', async () => {
  const { input } = await scenario();
  const mutations = [state => { state.fields[0].node_token = 'replacement'; },
    state => { state.fields[2].options[0].label = 'Changed'; },
    ...['dialog_id', 'root_token', 'tab_id', 'application_id', 'url'].map(key => state => { state.scope[key] += '-changed'; })];
  for (const mutate of mutations) {
    const { state, adapter, writes } = memory(input); const prepared = forms.plan(input); mutate(state);
    assert.equal((await forms.execute(prepared, adapter, options)).reason, 'page_changed');
    assert.deepEqual(writes, []);
  }
});

test('independent readback catches no-op writes and later reactive resets', async () => {
  const { input } = await scenario();
  let fake = memory(input); fake.adapter.write = async () => {};
  assert.equal((await forms.execute(forms.plan(input), fake.adapter, options)).reason, 'verification_failed');
  fake = memory(input); const write = fake.adapter.write;
  fake.adapter.write = async (...args) => {
    await write(...args);
    if (fake.writes.length === 2) fake.state.fields[0].value = 'reactive reset';
  };
  assert.equal((await forms.execute(forms.plan(input), fake.adapter, options)).reason, 'verification_failed');
  assert.equal(fake.writes.length, 2);
});

test('missing adapters, guard, resolver and native upload capability never fabricate success', async () => {
  const { input } = await scenario();
  assert.equal((await forms.execute(forms.plan(input), null, options)).reason, 'adapter_unavailable');
  assert.equal((await forms.execute(forms.plan(input), memory(input).adapter)).reason, 'guard_required');
  assert.equal((await forms.execute(forms.plan(input), memory(input).adapter, { guard })).reason, 'resume_resolver_required');
  const fake = memory(input); fake.adapter.canUpload = false;
  assert.equal((await forms.execute(forms.plan(input), fake.adapter, options)).reason, 'upload_adapter_unavailable');
  assert.deepEqual(fake.writes, []);
});

test('resume ID/hash/path must match fresh resolver before writes and immediately before upload', async () => {
  const { input } = await scenario();
  for (const key of ['id', 'sha256', 'path']) {
    const fake = memory(input), next = clone(resume);
    next[key] = key === 'sha256' ? '0'.repeat(64) : 'different';
    assert.equal((await forms.execute(forms.plan(input), fake.adapter, { guard, resumeResolver: () => next })).reason, 'resume_changed');
    assert.deepEqual(fake.writes, []);
  }
  let resolutions = 0;
  const fake = memory(input);
  const result = await forms.execute(forms.plan(input), fake.adapter, { guard,
    resumeResolver: () => { if (++resolutions === 2) throw new Error('private'); return resume; } });
  assert.equal(result.reason, 'resume_resolution_failed'); assert.equal(resolutions, 2);
  assert.equal(fake.writes.includes('resume'), false);
});

test('guard refusal prevents operations, adapter failure is sanitized, plans are single-use', async () => {
  const { input } = await scenario(), fake = memory(input);
  const blocked = await forms.execute(forms.plan(input), fake.adapter, { ...options, guard: phase => phase !== 'write' });
  assert.equal(blocked.reason, 'guard_refused'); assert.deepEqual(blocked.written, []);
  const prepared = forms.plan(input); fake.adapter.write = async () => { throw new Error('PRIVATE-CANARY'); };
  const result = await forms.execute(prepared, fake.adapter, options);
  assert.equal(result.reason, 'adapter_failed'); assert.equal(result.written.length, 1);
  assert.equal(JSON.stringify(result).includes('PRIVATE-CANARY'), false);
  assert.equal((await forms.execute(prepared, fake.adapter, options)).reason, 'invalid_plan');
});

test('browser global exports the exact same API without executing anything', () => {
  const context = { URL }; vm.runInNewContext(source, context);
  assert.deepEqual(Object.keys(context.ApplydForms), Object.keys(forms));
});

test('fixture native DOM double exercises real labels, selected values and independent File SHA-256', async () => {
  const { input, doc, adapter } = await scenario();
  const result = await forms.execute(forms.plan(input), adapter, options);
  assert.equal(result.status, 'verified');
  assert.equal(doc.getElementById('full-name').value, 'Example Applicant');
  assert.equal(doc.getElementById('years-python').valueAsNumber, 1);
  assert.equal(doc.getElementById('work-category').value, 'foreigner');
  assert.equal(doc.getElementById('sponsorship-yes').checked, true);
  assert.equal(doc.getElementById('sponsorship-no').checked, false);
  assert.equal(doc.getElementById('background-check').checked, true);
  assert.equal(doc.getElementById('history-end').value, '2025-12');
  const selected = result.observed.fields.find(field => field.field_id === 'resume').value;
  assert.equal(selected.sha256, resume.sha256); assert.equal(selected.id, resume.id); assert.equal(selected.path, resume.path);
  assert.equal(selected.size, bytes.length);
});

test('DOM upload never trusts receipt alone: absent file, wrong bytes and wrong identity fail', async () => {
  for (const mode of ['absent', 'wrong-bytes', 'wrong-id']) {
    const doc = parse(), adapter = adapterFor(doc, async ({ input }) => {
      if (mode !== 'absent') input.files = [new File([mode === 'wrong-bytes' ? 'different' : bytes], 'synthetic.pdf')];
      return { ...resume, id: mode === 'wrong-id' ? 'wrong-release' : resume.id };
    });
    const input = inputFor(await adapter.observe());
    const result = await forms.execute(forms.plan(input), adapter, options);
    assert.equal(result.status, 'handoff');
    assert.equal(result.reason, mode === 'wrong-bytes' ? 'verification_failed' : 'upload_identity_mismatch');
  }
});

test('DOM adapter without upload stops before any text write; unbound existing file has no invented identity', async () => {
  const doc = parse(), adapter = adapterFor(doc), input = inputFor(await adapter.observe());
  const result = await forms.execute(forms.plan(input), adapter, options);
  assert.equal(result.reason, 'upload_adapter_unavailable'); assert.equal(doc.getElementById('full-name').value, '');
  doc.getElementById('resume').files = [new File([bytes], 'synthetic.pdf')];
  const field = (await adapter.observe()).fields.find(item => item.type === 'file');
  assert.equal(field.value.sha256, resume.sha256); assert.equal(field.value.id, null); assert.equal(field.value.path, null);
});

test('DOM scope is checked at write boundary; no external caller can dispatch arbitrary actions', async () => {
  const { adapter, input } = await scenario();
  await assert.rejects(adapter.write({ kind: 'click', field_id: 'resume' }, input.observation.scope), error => error.code === 'invalid_plan');
  const prepared = forms.plan(input);
  const doc = parse(); const other = adapterFor(doc);
  // A different DOM adapter has different node tokens/layout and no upload bridge.
  assert.equal((await forms.execute(prepared, other, options)).status, 'handoff');
  const live = await scenario();
  const result = await forms.execute(forms.plan(live.input), live.adapter, { ...options,
    guard: phase => {
      if (phase === 'write') live.doc.getElementById('application-dialog').attrs['aria-label'] = 'Changed dialog';
      return true;
    } });
  assert.equal(result.reason, 'page_changed');
  assert.equal(live.doc.getElementById('full-name').value, '');
});

test('DOM async file hashing rechecks actual File identity and reads current other values', async () => {
  const doc = parse();
  const first = new File([bytes], 'first.pdf');
  doc.getElementById('resume').files = [first];
  const original = doc.defaultView.crypto;
  doc.defaultView.crypto = { subtle: { async digest(...args) {
    doc.getElementById('resume').files = [new File(['changed'], 'changed.pdf')];
    return original.subtle.digest(...args);
  } } };
  await assert.rejects(adapterFor(doc).observe(), error => error.code === 'page_changed');
  doc.getElementById('resume').files = [first];
  doc.defaultView.crypto = { subtle: { async digest(...args) {
    doc.getElementById('full-name').value = 'Current actual value';
    return original.subtle.digest(...args);
  } } };
  assert.equal((await adapterFor(doc).observe()).fields[0].value, 'Current actual value');
});

test('changed fixture layout and custom widgets stop; parent fixture has no submission/network affordances', async () => {
  const changed = parse(html.replace('<div id="extra-fields"></div>',
    '<div id="extra-fields"><label for="unknown-qualification">Unknown qualification</label><input id="unknown-qualification" required></div>'));
  const { input } = await scenario();
  const live = await adapterFor(changed).observe();
  assert.equal(forms.plan({ ...input, observation: live }).reason, 'unsupported_form');
  const custom = parse(html.replace('<div id="extra-fields"></div>', '<div id="extra-fields" role="combobox" aria-label="New widget"></div>'));
  await assert.rejects(adapterFor(custom).observe(), error => error.code === 'unsupported_form');
  assert.match(html, /connect-src 'none'/); assert.match(html, /form-action 'none'/);
  assert.doesNotMatch(html, /<form\b|type=["']submit["']|<button\b|<iframe\b|<link\b|\b(?:src|action)\s*=/i);
});

test('all matching prefilled fields and verified LinkedIn asset perform zero writes/uploads/path operations', async () => {
  const { input } = await scenario(), fake = memory(input);
  const asset = { account_id: 'synthetic-account', asset_id: 'opaque-asset', revision: '1' };
  for (const action of forms.plan(input).actions) {
    const field = fake.state.fields.find(f => f.field_id === action.field_id);
    field.valid = true;
    if (action.kind === 'upload_resume') { field.value = null; field.platform_asset = asset; }
    else field.value = action.value;
  }
  fake.adapter.canUpload = false;
  let reads = 0, verified = 0;
  const observe = fake.adapter.observe;
  fake.adapter.observe = () => { reads++; return observe(); };
  const result = await forms.execute(forms.plan(input), fake.adapter, { ...options,
    resumeVerifier: ({ asset: actual, resume: expected }) => {
      assert.deepEqual(actual, asset); assert.deepEqual(expected, resume); verified++; return true;
    } });
  assert.equal(result.status, 'verified'); assert.deepEqual(fake.writes, []);
  assert.equal(result.skipped.length, 9); assert.equal(reads, 2); assert.ok(verified > 0);
});

test('unmapped or changed selected asset falls back to upload, or typed handoff without bridge', async () => {
  for (const hasBridge of [true, false]) {
    const { input } = await scenario(), fake = memory(input);
    fake.state.fields.find(f => f.type === 'file').platform_asset = { account_id: 'a', asset_id: 'changed', revision: '2' };
    fake.adapter.canUpload = hasBridge;
    const write = fake.adapter.write;
    fake.adapter.write = async (...args) => {
      await write(...args);
      if (args[0].kind === 'upload_resume') delete fake.state.fields.find(f => f.type === 'file').platform_asset;
    };
    const result = await forms.execute(forms.plan(input), fake.adapter, { ...options, resumeVerifier: () => false });
    assert.equal(result.status, hasBridge ? 'verified' : 'handoff');
    assert.equal(fake.writes.includes('resume'), hasBridge);
    if (!hasBridge) assert.deepEqual(fake.writes, []);
  }
});

test('a later write resetting a skipped prefilled value is detected; observations are bounded', async () => {
  const { input } = await scenario(), fake = memory(input);
  const first = forms.plan(input).actions[0]; fake.state.fields[0].value = first.value; fake.state.fields[0].valid = true;
  const write = fake.adapter.write;
  let reads = 0; const observe = fake.adapter.observe;
  fake.adapter.observe = () => { reads++; return observe(); };
  fake.adapter.write = async (...args) => { await write(...args); fake.state.fields[0].value = 'reset'; };
  const result = await forms.execute(forms.plan(input), fake.adapter, options);
  assert.equal(result.reason, 'verification_failed'); assert.equal(fake.writes.length, 1); assert.equal(reads, 2);
});

test('selected platform asset is independently observed through concrete DOM adapter', async () => {
  const doc = parse(); doc.location.href = 'https://www.linkedin.com/jobs/view/123';
  const asset = { account_id: 'synthetic', asset_id: 'opaque', revision: '1' };
  const adapter = forms.createDOMAdapter({ getRoot: () => doc.getElementById('application-dialog'),
    getScope: () => ({ application_id: 'app-synthetic', tab_id: 'tab', kind: 'easy_apply', url: doc.location.href }),
    selectedResume: () => asset });
  const input = inputFor(await adapter.observe());
  const result = await forms.execute(forms.plan(input), adapter, { ...options, resumeVerifier: ({asset: actual}) => equalAsset(actual) });
  function equalAsset(actual) { assert.deepEqual(actual, asset); return true; }
  assert.equal(result.status, 'verified'); assert.equal(result.written.includes('resume'), false);
});

test('fresh guard blocks a changed approval/STOP before mismatch write or skipped-field final read', async () => {
  const { input } = await scenario(), fake = memory(input);
  let checks=0;
  const result=await forms.execute(forms.plan(input),fake.adapter,{...options,guard:phase=>{checks++;return phase!=='write';}});
  assert.equal(result.reason,'guard_refused');assert.deepEqual(fake.writes,[]);assert.ok(checks>1);
});

test('async selected-asset observation cannot hide a concurrent field reset', async () => {
  const doc=parse();let read=0;
  const adapter=forms.createDOMAdapter({getRoot:()=>doc.getElementById('application-dialog'),
    getScope:()=>({application_id:'app-synthetic',tab_id:'tab',kind:'synthetic_application',url:doc.location.href}),
    selectedResume:async()=>{read++;doc.getElementById('full-name').value='Actual reactive value';return {account_id:'synthetic',asset_id:'opaque',revision:'1'};}});
  const observed=await adapter.observe();assert.equal(observed.fields[0].value,'Actual reactive value');assert.equal(read,1);
});

for (const target of ['initial observation', 'readback observation', 'field write', 'upload', 'resolve', 'verify', 'remember']) {
  test(`STOP during durable reservation prevents ${target}`, async () => {
    const T = require('../scripts/telemetry.js');
    const { input } = await scenario(), fake = memory(input);
    const asset = {account_id: 'synthetic', asset_id: 'opaque', revision: '1'};
    const file = fake.state.fields.find(f => f.type === 'file');
    if (target === 'verify') file.platform_asset = asset;
    const calls = [];
    let stopped = false, paused = false, observations = 0;
    const originalObserve = fake.adapter.observe, originalWrite = fake.adapter.write;
    fake.adapter.observe = async () => { calls.push('observe'); return originalObserve(); };
    fake.adapter.write = async (...args) => {
      calls.push(args[0].kind === 'upload_resume' ? 'upload' : 'write');
      await originalWrite(...args);
      if (target === 'remember' && args[0].kind === 'upload_resume') file.platform_asset = asset;
    };
    const desired = {'initial observation':'form_observation', 'readback observation':'form_observation',
      'field write':'form_filling', upload:'resume_upload', resolve:'resume_resolution', verify:'resume_reuse', remember:'resume_reuse'}[target];
    let before;
    const telemetry = T.create({context:{run_id:'synthetic-run'},processId:'synthetic-process',emit:async row => {
      if (row.outcome !== 'open') return;
      if (row.phase === 'form_observation') observations++;
      if (!paused && row.phase === desired && (target !== 'readback observation' || observations === 2)) {
        paused = true;
        before = [...calls];
        // Yield to a control change while the durable reservation is in flight.
        await new Promise(resolve => setImmediate(() => { stopped = true; resolve(); }));
      }
    }});
    const result = await forms.execute(forms.plan(input),fake.adapter,{telemetry,guard:async () => !stopped,
      resumeResolver:async () => {calls.push('resolve');return clone(resume);},
      resumeVerifier:async () => {calls.push('verify');return true;},
      rememberResume:async () => {calls.push('remember');return true;}});
    assert.equal(paused,true);
    assert.equal(result.reason,'guard_refused');
    assert.deepEqual(calls,before,'no adapter/resolver/receipt operation may run after STOP');
  });
}

for (const kind of ['write', 'upload']) for (const outcome of ['refused', 'failed']) {
  test(`${outcome} ${kind} counts only actual attempted native operations`, async () => {
    const T=require('../scripts/telemetry.js');
    const {input}=await scenario(),fake=memory(input),rows=[];
    let stopped=false,calls=0;
    const phase=kind==='upload'?'resume_upload':'form_filling';
    const counter=kind==='upload'?'uploads':'field_writes';
    const original=fake.adapter.write;
    fake.adapter.write=async(...args)=>{
      if ((args[0].kind==='upload_resume')===(kind==='upload')) {calls++;throw Error('synthetic native failure');}
      return original(...args);
    };
    const telemetry=T.create({context:{run_id:'synthetic-run'},processId:'synthetic-process',emit:async row=>{
      rows.push(row);
      if(outcome==='refused' && row.phase===phase && row.outcome==='open') {
        await new Promise(resolve=>setImmediate(()=>{stopped=true;resolve();}));
      }
    }});
    const result=await forms.execute(forms.plan(input),fake.adapter,{...options,telemetry,guard:()=>!stopped});
    assert.equal(result.reason,outcome==='refused'?'guard_refused':'adapter_failed');
    const reserved=rows.find(r=>r.phase===phase && r.outcome==='open');
    const finished=rows.find(r=>r.phase===phase && r.outcome==='failed');
    assert.equal(reserved.counters[counter],0);
    assert.equal(finished.counters[counter],outcome==='refused'?0:1);
    assert.equal(calls,outcome==='refused'?0:1);
    assert.equal(rows.filter(r=>r.phase===phase && r.outcome!=='open').reduce((n,r)=>n+r.counters[counter],0),calls);
  });
}

test('STOP while hashing prevents the subsequent native platform asset callback', async () => {
  const doc=parse();let stopped=false,selections=0;
  doc.getElementById('resume').files=[{name:'synthetic.pdf',size:bytes.length,arrayBuffer:async()=>{
    stopped=true;return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
  }}];
  const adapter=forms.createDOMAdapter({getRoot:()=>doc.getElementById('application-dialog'),
    getScope:()=>({application_id:'app-synthetic',tab_id:'tab',kind:'synthetic_application',url:doc.location.href}),
    selectedResume:()=>{selections++;return null;}});
  await assert.rejects(adapter.observeGuarded(()=>{if(stopped)throw Error('guard_refused');}),/guard_refused/);
  assert.equal(selections,0);
});
