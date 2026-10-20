/* CommonJS: require('./form_handlers.js'); browser: globalThis.ApplydForms.
 * No driver, network, navigation, click or submission operations. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ApplydForms = factory();
})(globalThis, function () {
  'use strict';
  const plans = new WeakMap();
  const commands = new WeakSet();
  const nodeTokens = new WeakMap();
  let nodeCounter = 0;
  const types = new Set(['text', 'number', 'select', 'radio', 'checkbox', 'file']);
  const scopes = new Set(['easy_apply', 'ats_application', 'synthetic_application']);
  const json = value => JSON.stringify(value);
  const copy = value => JSON.parse(json(value));
  const equal = (a, b) => json(a) === json(b);
  const nonempty = value => typeof value === 'string' && value.trim().length > 0;
  const freeze = value => {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  };
  class FormError extends Error {
    constructor(code) { super(code); this.code = code; }
  }
  const fail = code => { throw new FormError(code); };
  function handoff(code, kind, written = [], observed = null) {
    return freeze({ status: 'handoff', reason: code,
      next: kind === 'ats_application' && code === 'unsupported_form'
        ? 'agent_review_allowed_ats_fallback' : 'agent_review', written, observed });
  }
  function checkScope(scope) {
    if (!scope || !scopes.has(scope.kind) ||
        !['application_id', 'tab_id', 'dialog_id', 'dialog_label', 'url', 'root_token'].every(key => nonempty(scope[key]))) {
      fail('unsupported_form');
    }
    let url;
    try { url = new URL(scope.url); } catch { fail('unsupported_form'); }
    if (url.username || url.password || !['https:', 'http:', 'file:'].includes(url.protocol) ||
        (url.protocol === 'file:' && scope.kind !== 'synthetic_application')) fail('unsupported_form');
    const linkedin = /(^|\.)linkedin\.com$/i.test(url.hostname);
    if (linkedin && (!url.pathname.startsWith('/jobs/') || scope.kind !== 'easy_apply')) fail('unsupported_form');
  }
  function descriptor(field) {
    return { field_id: field.field_id, label: field.label, section: field.section,
      type: field.type, required: field.required, writable: field.writable,
      options: field.options, node_token: field.node_token, constraints: field.constraints };
  }
  function layout(observation) {
    return json({ scope: observation.scope, fields: observation.fields.map(descriptor) });
  }
  function checkObservation(observation) {
    checkScope(observation?.scope);
    if (!Array.isArray(observation.fields) || !observation.fields.length) fail('unsupported_form');
    const ids = new Set();
    for (const field of observation.fields) {
      if (!field || !nonempty(field.field_id) || ids.has(field.field_id) || !nonempty(field.label) ||
          typeof field.section !== 'string' || !nonempty(field.node_token) || !types.has(field.type) ||
          typeof field.required !== 'boolean' || typeof field.writable !== 'boolean' ||
          !Array.isArray(field.options) || !field.constraints || typeof field.constraints !== 'object') fail('unsupported_form');
      ids.add(field.field_id);
      if (/(?:save|update|sync|add|edit).*\bprofile\b|\bprofile\b.*(?:save|update|sync|edit)/i.test(field.label)) fail('unsupported_form');
      const labels = new Set(), values = new Set();
      for (const option of field.options) {
        const key = typeof option?.label === 'string' ? option.label.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase() : '';
        if (!key || labels.has(key) || typeof option.value !== 'string' || values.has(option.value) ||
            typeof option.disabled !== 'boolean') fail('unsupported_form');
        labels.add(key); values.add(option.value);
      }
      if (['select', 'radio'].includes(field.type) ? !field.options.length : field.options.length) fail('unsupported_form');
    }
  }
  function checkResume(resume) {
    if (!resume || !['id', 'path', 'sha256'].every(key => nonempty(resume[key])) ||
        !/^[a-f0-9]{64}$/.test(resume.sha256)) fail('resume_required');
    return { id: resume.id, path: resume.path, sha256: resume.sha256 };
  }
  function answerValue(row, field) {
    const expectedType = { text: 'text', number: 'number', select: 'single_select', radio: 'single_select', checkbox: 'boolean' }[field.type];
    if (row.type !== expectedType || !equal(row.options, field.options.map(option => option.label))) fail('unsupported_form');
    const value = row.answer;
    if (field.type === 'text' && (!nonempty(value) || (field.constraints.maxLength >= 0 && value.length > field.constraints.maxLength))) fail('invalid_answer');
    if (field.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) fail('invalid_answer');
    if (field.type === 'number') {
      if ((field.constraints.min !== '' && value < Number(field.constraints.min)) ||
          (field.constraints.max !== '' && value > Number(field.constraints.max))) fail('invalid_answer');
    }
    if (field.type === 'checkbox' && (typeof value !== 'boolean' || (field.required && !value))) fail('invalid_answer');
    if (['select', 'radio'].includes(field.type)) {
      const option = field.options.find(item => item.label === value);
      if (!option || option.disabled) fail('unsupported_form');
      return option.value;
    }
    return value;
  }
  /** Pure planning. approved is a parent-validated immutable answer snapshot. */
  function plan({ observation, approved, bindings, resume = null } = {}) {
    let kind;
    try {
      checkScope(observation?.scope);
      kind = observation.scope.kind;
      checkObservation(observation);
      if (!approved || !nonempty(approved.snapshot_id) || approved.application_id !== observation.scope.application_id ||
          !Array.isArray(approved.answers) || !Array.isArray(bindings)) fail('approval_required');
      const rows = new Map();
      for (const row of approved.answers) {
        if (!row || !nonempty(row.occurrence_id) || rows.has(row.occurrence_id) || row.resolution !== 'resolved' ||
            row.application_id !== approved.application_id) fail('approval_required');
        rows.set(row.occurrence_id, row);
      }
      const fields = new Map(observation.fields.map(field => [field.field_id, field]));
      const boundFields = new Set(), boundRows = new Set(), actions = [];
      for (const binding of bindings) {
        const field = fields.get(binding.field_id);
        if (!field || boundFields.has(binding.field_id) || !field.writable) fail('unsupported_form');
        boundFields.add(binding.field_id);
        if (field.type === 'file') {
          if (binding.occurrence_id !== undefined || binding.history_key !== undefined) fail('unsupported_form');
          if (!['resume', 'cv', 'resume/cv', 'upload resume', 'upload cv'].includes(field.label.trim().toLowerCase())) fail('unsupported_form');
          actions.push({ field_id: field.field_id, kind: 'upload_resume', resume: checkResume(resume) });
          continue;
        }
        const row = rows.get(binding.occurrence_id);
        if (!row || boundRows.has(row.occurrence_id)) fail('approval_required');
        boundRows.add(row.occurrence_id);
        if (row.question !== field.label || row.required !== field.required ||
            (row.field_id !== undefined && row.field_id !== field.field_id) ||
            (row.field_id === undefined && observation.fields.filter(item => item.label === field.label).length !== 1)) fail('unsupported_form');
        const value = answerValue(row, field);
        const history = /^(work|employment) history$/i.test(field.section);
        if (history || binding.history_key !== undefined) {
          if (!nonempty(binding.history_key) || !approved.saved_history ||
              !Object.hasOwn(approved.saved_history, binding.history_key) ||
              !equal(approved.saved_history[binding.history_key], row.answer)) fail('history_mismatch');
        }
        actions.push({ field_id: field.field_id, kind: 'set_value', value, occurrence_id: row.occurrence_id });
      }
      // Omitted prepared rows are never silently dropped, even if optional.
      if (boundRows.size !== rows.size) fail('approval_required');
      if (observation.fields.some(field => !boundFields.has(field.field_id) &&
          (field.required || /^(work|employment) history$/i.test(field.section)))) fail('unsupported_form');
      if (actions.filter(action => action.kind === 'upload_resume').length > 1) fail('unsupported_form');
      const result = freeze({ status: 'planned', snapshot_id: approved.snapshot_id,
        scope: copy(observation.scope), actions: copy(actions) });
      plans.set(result, { observation: freeze(copy(observation)), actions: freeze(copy(actions)) });
      return result;
    } catch (error) {
      return handoff(error instanceof FormError ? error.code : 'unsupported_form', kind);
    }
  }
  function matches(field, action) {
    if (action.kind === 'upload_resume') {
      return field.valid !== false && field.value && ['id', 'sha256', 'path'].every(key => field.value[key] === action.resume[key]);
    }
    return equal(field.value, action.value) && field.valid !== false;
  }
  /** Execute only plans minted by this module instance. No retry after a write. */
  async function execute(prepared, adapter, { guard, resumeResolver } = {}) {
    const entry = plans.get(prepared), written = [];
    const kind = prepared?.scope?.kind;
    if (!entry) return handoff('invalid_plan', kind);
    if (!adapter || typeof adapter.observe !== 'function' || typeof adapter.write !== 'function') return handoff('adapter_unavailable', kind);
    if (typeof guard !== 'function') return handoff('guard_required', kind);
    if (entry.actions.some(action => action.kind === 'upload_resume') && typeof resumeResolver !== 'function') return handoff('resume_resolver_required', kind);
    // Once observation begins the plan is single-use; reobserve and replan.
    plans.delete(prepared);
    let observed = null;
    async function guarded(phase, fn) {
      let allowed = false;
      try { allowed = await guard(phase); } catch { fail('guard_refused'); }
      if (allowed !== true) fail('guard_refused');
      return fn();
    }
    async function observe() {
      // No approved values or expected snapshot is passed to the reader.
      observed = await guarded('observe', () => adapter.observe());
      checkObservation(observed);
      if (layout(observed) !== layout(entry.observation)) fail('page_changed');
      return observed;
    }
    async function resolve(action) {
      let selection;
      try { selection = checkResume(await guarded('resolve_resume', resumeResolver)); }
      catch (error) { if (error.code === 'guard_refused') throw error; fail('resume_resolution_failed'); }
      if (!equal(selection, action.resume)) fail('resume_changed');
    }
    try {
      await observe();
      for (const action of entry.actions) if (action.kind === 'upload_resume') {
        if (adapter.canUpload !== true) fail('upload_adapter_unavailable');
        await resolve(action);
      }
      for (const action of entry.actions) {
        const current = await observe();
        // Recheck all earlier writes before progressing, including reactive resets.
        for (const id of written) {
          const previous = entry.actions.find(item => item.field_id === id);
          if (!matches(current.fields.find(field => field.field_id === id), previous)) fail('verification_failed');
        }
        if (action.kind === 'upload_resume') await resolve(action);
        const command = freeze({ ...copy(action), expected_layout: layout(entry.observation) });
        commands.add(command);
        // Reserve before the adapter boundary. Failures may have partially written.
        try { await guarded('write', () => {
          written.push(action.field_id);
          return adapter.write(command, prepared.scope);
        }); }
        finally { commands.delete(command); }
        const after = await observe();
        if (!matches(after.fields.find(field => field.field_id === action.field_id), action)) fail('verification_failed');
      }
      const final = await observe();
      if (!entry.actions.every(action => matches(final.fields.find(field => field.field_id === action.field_id), action))) fail('verification_failed');
      return freeze({ status: 'verified', snapshot_id: prepared.snapshot_id, written,
        observed: copy(final) });
    } catch (error) {
      // Content-free reasons: raw adapter errors can contain private data.
      return handoff(error instanceof FormError ? error.code : 'adapter_failed', kind, written,
        observed ? copy(observed) : null);
    }
  }

  /** Native DOM adapter. Parent supplies the CURRENT dialog and actual tab scope.
   * upload({input,resume}) is a trusted native file chooser bridge, never a URL. */
  function createDOMAdapter({ getRoot, getScope, upload } = {}) {
    const receipts = new WeakMap();
    const token = node => {
      if (!nodeTokens.has(node)) nodeTokens.set(node, `node-${++nodeCounter}`);
      return nodeTokens.get(node);
    };
    const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
    function accessible(node) {
      const labelledBy = node.getAttribute('aria-labelledby');
      if (labelledBy) {
        const parts = labelledBy.trim().split(/\s+/).map(id => node.ownerDocument.getElementById(id));
        if (parts.some(item => !item)) fail('unsupported_form');
        return parts.map(text).join(' ');
      }
      const aria = node.getAttribute('aria-label');
      if (aria) return aria;
      if (node.labels?.length === 1) return text(node.labels[0]);
      if (node.tagName === 'FIELDSET') return text(Array.from(node.children).find(child => child.tagName === 'LEGEND'));
      return '';
    }
    function visible(node) {
      for (let current = node; current?.nodeType === 1; current = current.parentElement) {
        const style = current.ownerDocument.defaultView.getComputedStyle(current);
        if (current.hidden || current.getAttribute('aria-hidden') === 'true' || current.inert ||
            style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)) return false;
      }
      return node.getClientRects().length > 0;
    }
    function collect() {
      const root = typeof getRoot === 'function' && getRoot();
      if (!root?.isConnected || !visible(root) || !(root.tagName === 'DIALOG' || root.getAttribute('role') === 'dialog')) fail('unsupported_form');
      if (!nonempty(root.id) || !nonempty(accessible(root))) fail('unsupported_form');
      const supplied = typeof getScope === 'function' && getScope();
      const scope = { ...supplied, dialog_id: root.id, dialog_label: accessible(root), root_token: token(root) };
      checkScope(scope);
      if (scope.url !== root.ownerDocument.location.href) fail('page_changed');
      if (root.querySelector('[contenteditable]:not([contenteditable="false"]), [role="combobox"], [role="listbox"], [role="slider"], [role="switch"], [role="checkbox"], [role="radio"], [role="textbox"], [role="spinbutton"]')) fail('unsupported_form');
      for (const required of root.querySelectorAll('[aria-required="true"]')) {
        if (!['INPUT', 'TEXTAREA', 'SELECT', 'FIELDSET'].includes(required.tagName)) fail('unsupported_form');
      }
      const fields = [], targets = new Map(), radioNames = new Set();
      for (const node of root.querySelectorAll('input, textarea, select')) {
        if (node.closest('[role="dialog"], dialog') !== root) fail('unsupported_form');
        const nativeType = node.tagName === 'INPUT' ? node.type : node.tagName.toLowerCase();
        if (['hidden', 'submit', 'button', 'reset', 'image'].includes(nativeType)) continue;
        if (!visible(node)) fail('unsupported_form');
        const fieldset = node.closest('fieldset');
        let nodes = [node], fieldId = node.id, label = accessible(node), type;
        if (nativeType === 'radio') {
          if (!node.name || !fieldset || !root.contains(fieldset)) fail('unsupported_form');
          if (radioNames.has(node.name)) continue;
          radioNames.add(node.name);
          nodes = Array.from(root.querySelectorAll('input')).filter(item => item.type === 'radio' && item.name === node.name);
          if (nodes.some(item => item.closest('fieldset') !== fieldset || !visible(item))) fail('unsupported_form');
          fieldId = fieldset.id; label = accessible(fieldset); type = 'radio';
        } else if (nativeType === 'select') {
          if (node.multiple || node.size > 1) fail('unsupported_form');
          type = 'select';
        } else if (['text', 'email', 'tel', 'url', 'textarea'].includes(nativeType)) type = 'text';
        else if (['number', 'checkbox', 'file'].includes(nativeType)) type = nativeType;
        else fail('unsupported_form');
        if (type === 'file' && node.multiple) fail('unsupported_form');
        if (!fieldId || targets.has(fieldId)) fail('unsupported_form');
        const options = type === 'radio' ? nodes.map(item => ({ label: accessible(item), value: item.value, disabled: item.matches(':disabled') })) :
          type === 'select' ? Array.from(node.options, item => ({ label: item.label, value: item.value,
            disabled: item.disabled || (item.parentElement.tagName === 'OPTGROUP' && item.parentElement.disabled) })) : [];
        const selected = nodes.filter(item => item.checked);
        if (type === 'radio' && selected.length > 1) fail('unsupported_form');
        const value = type === 'checkbox' ? node.checked : type === 'radio' ? (selected[0]?.value ?? null) :
          type === 'number' ? (Number.isFinite(node.valueAsNumber) ? node.valueAsNumber : null) : type === 'file' ? null : node.value;
        fields.push({ field_id: fieldId, label, section: fieldset && type !== 'radio' ? accessible(fieldset) : '',
          type, required: nodes.some(item => item.required || item.getAttribute('aria-required') === 'true'),
          writable: nodes.every(item => !item.matches(':disabled') && !item.readOnly), options,
          node_token: nodes.map(token).join(','),
          constraints: { native_type: nativeType, min: node.min || '', max: node.max || '', step: node.step || '',
            pattern: node.pattern || '', maxLength: node.maxLength ?? -1, accept: node.accept || '' },
          value, valid: nodes.every(item => item.validity?.valid !== false) });
        targets.set(fieldId, { node, nodes, type });
      }
      const observation = { scope, fields };
      checkObservation(observation);
      return { root, observation, targets };
    }
    async function observe() {
      const { root, observation, targets } = collect();
      const files = new Map();
      for (const field of observation.fields.filter(item => item.type === 'file')) {
        const input = targets.get(field.field_id).node;
        if (input.files.length > 1) fail('unsupported_form');
        const file = input.files[0];
        files.set(field.field_id, { input, file });
        if (!file) continue;
        const crypto = root.ownerDocument.defaultView.crypto;
        if (!crypto?.subtle) fail('upload_verification_unavailable');
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())),
          byte => byte.toString(16).padStart(2, '0')).join('');
        const receipt = receipts.get(input);
        const bound = receipt?.file === file && receipt.selection.sha256 === digest;
        field.value = { id: bound ? receipt.selection.id : null, path: bound ? receipt.selection.path : null,
          sha256: digest, name: file.name, size: file.size };
      }
      const current = collect().observation;
      if (layout(current) !== layout(observation)) fail('page_changed');
      for (const [id, { input, file }] of files) {
        if (input.files.length !== (file ? 1 : 0) || input.files[0] !== file) fail('page_changed');
        current.fields.find(field => field.field_id === id).value = observation.fields.find(field => field.field_id === id).value;
      }
      return current;
    }
    async function write(action, expectedScope) {
      if (!commands.has(action)) fail('invalid_plan');
      const { root, observation, targets } = collect();
      if (!equal(observation.scope, expectedScope) || action.expected_layout !== layout(observation)) fail('page_changed');
      const target = targets.get(action.field_id);
      if (!target) fail('page_changed');
      const { node, nodes, type } = target;
      if (action.kind === 'upload_resume') {
        if (type !== 'file' || typeof upload !== 'function') fail('upload_adapter_unavailable');
        receipts.delete(node);
        const receipt = checkResume(await upload({ input: node, resume: freeze(copy(action.resume)) }));
        if (!equal(receipt, action.resume) || node.files.length !== 1) fail('upload_identity_mismatch');
        receipts.set(node, { file: node.files[0], selection: receipt });
        return;
      }
      if (action.kind !== 'set_value' || type === 'file') fail('invalid_plan');
      const view = root.ownerDocument.defaultView;
      function set(element, property, value) {
        const proto = element.tagName === 'SELECT' ? view.HTMLSelectElement.prototype :
          element.tagName === 'TEXTAREA' ? view.HTMLTextAreaElement.prototype : view.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, property)?.set;
        if (!setter) fail('unsupported_form');
        setter.call(element, value);
      }
      if (type === 'radio') {
        const option = nodes.find(item => item.value === action.value && !item.matches(':disabled'));
        if (!option) fail('page_changed');
        set(option, 'checked', true);
        option.dispatchEvent(new view.Event('input', { bubbles: true }));
        option.dispatchEvent(new view.Event('change', { bubbles: true }));
      } else {
        set(node, type === 'checkbox' ? 'checked' : 'value', action.value);
        node.dispatchEvent(new view.Event('input', { bubbles: true }));
        node.dispatchEvent(new view.Event('change', { bubbles: true }));
      }
    }
    return Object.freeze({ observe, write, canUpload: typeof upload === 'function' });
  }
  return Object.freeze({ plan, execute, createDOMAdapter, FormError });
});
