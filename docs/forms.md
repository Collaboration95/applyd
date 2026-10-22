# Common application fields

`scripts/form_handlers.js` is plain JavaScript with no dependencies, driver,
filesystem reads or network access. It supports native text/textarea, number,
single select, radio group, boolean checkbox and one resume file input in the
current application dialog. Custom widgets and unfamiliar layouts return a typed
handoff. There are no Submit, Next, Send, navigation, keyboard or click actions.

Node.js 18+ exposes `require('./scripts/form_handlers.js')`. Loading the same file
as a browser script exposes `globalThis.ApplydForms`. Both export exactly:

```js
plan({ observation, approved, bindings, resume = null })
// Synchronous: {status: 'planned', snapshot_id, scope, actions}, or handoff.
execute(preparedPlan, adapter, { guard, resumeResolver, resumeVerifier, rememberResume, telemetry })
// Async: {status: 'verified', snapshot_id, written, observed}, or handoff.
createDOMAdapter({ getRoot, getScope, upload, selectedResume })
// {observe(), write(command, expectedScope), canUpload}
FormError // error.code is a content-free typed reason
```

Plans are frozen and valid only in the module instance that created them; JSON
round-tripping does not produce an executable plan. Once execution observes the
page, that plan is consumed, even on failure. Reobserve/revalidate before another
attempt. `verified` means these field values were independently observed; it is
not submission authorization or evidence of an application.

## Parent workflow hook

Invoke this helper inside the runner's existing prepare/review adapter while
holding its single browser-writer lock. No runner/workspace integration is added
by this module. The parent still owns answer preparation, all policy/freshness
checks, immutable private records and any subsequent review/submission workflow.

1. Inspect the actual current application dialog and tab/job identity. Capture
   current questions/options through the adapter; prepare answers through
   `answers.prepare_answers`, then require `answers.validate_snapshot` success
   for this application. Keep the exact prepared rows, occurrence IDs, units,
   scopes, provenance and approved option text. Do not rebuild answers from DOM
   defaults or use this helper to decide consent/eligibility.
2. Resolve the application resume through `scripts/resolve_resume.py` immediately
   before planning. Pass its exact `{id, path, sha256}`; retain its other release
   metadata privately. For history fields, supply exact current saved facts,
   already checked for scope/provenance/freshness. Never infer dates or years.
3. Bind each non-file control to its exact prepared `occurrence_id`, by stable
   semantic field ID. Do not bind by list position or fuzzy wording. The question
   must exactly match its accessible label; type, required flag and the complete
   ordered option-label list must match. Use explicit prepared `field_id` values
   to distinguish identical repeated labels. If a label includes a required
   marker, preserve that exact label during question preparation.
4. Call `plan`, inspect its status, then call `execute` with a live guard and
   fresh resolver callback. The guard must return exactly `true` before every
   observe, resolver and write call; exceptions/refusals stop. It must enforce
   current/frozen settings, work budget/deadline, answer/history validity, actual
   job identity, safety challenges and writer ownership. Keep browser tool calls
   bounded by the parent's normal timeout. No automatic retry or rollback occurs.
5. Preserve the result privately. `written` lists attempted fields, including an
   adapter call that might have partially failed. `observed` is the last actual
   observation, possibly from before the failure; it is never manufactured to
   match answers. Reconcile any partial writes before continuing. Never replay a
   submit/uncertain application through this helper.

For example, after the parent has validated an actual prepared row:

```js
const F = globalThis.ApplydForms; // Node: require('./scripts/form_handlers.js')
const adapter = F.createDOMAdapter({
  getRoot: () => currentApplicationDialog(),
  getScope: () => observedCurrentScope(),
  upload: trustedNativeResumeUpload // omit if unavailable
});
const observation = await adapter.observe(); // catch FormError.code -> handoff
const approved = {
  application_id: currentApplicationId,
  snapshot_id: validatedPrepared.snapshot_version,
  answers: validatedPrepared.answers, // complete rows for this dialog/step
  saved_history: validatedExactHistory // { 'job-1.company': 'Example Cedar Labs' }
};
const prepared = F.plan({
  observation, approved, resume: freshlyResolvedResume,
  bindings: currentSemanticBindings
});
if (prepared.status === 'planned') {
  const result = await F.execute(prepared, adapter, {
    guard: async phase => parentLiveGuard(phase), // exactly true on success
    resumeResolver: async () => parentResolveCurrentApplicationResume()
  });
  persistPrivateFormResult(result);
} else {
  persistPrivateFormResult(prepared);
}
```

These callback names describe parent-owned hooks; they are not new implemented
CLI commands. All approved rows supplied to one plan must be bound exactly once.
For multi-step forms the parent validates the complete saved snapshot and then
passes exactly the rows belonging to the current observed step. Never drop an
unknown required question to make a plan succeed.

## Data and adapter contract

`approved` is trusted parent input, not page content. Each prepared row retains
at least `{application_id, occurrence_id, question, type, options, required,
resolution: 'resolved', answer}`, plus its existing saved provenance. `field_id`
is optional only for unique labels. Types map as follows:

| Observed control | Prepared type | Approved answer |
| --- | --- | --- |
| text/textarea | `text` | nonempty exact string |
| number | `number` | finite number, no conversion/inference |
| native select/radio group | `single_select` | exact observed option label |
| standalone checkbox | `boolean` | true/false; required must be true |
| file input | separate resolver selection | exact id/path/SHA-256 |

Boolean-to-Yes or number-to-string option coercion is unsupported. Select/radio
`options` includes every option, including disabled placeholders, in observed
order. Choosing a disabled option is refused. The native value underlying the
exact chosen label is verified after writing. Ambiguous labels/option values,
duplicate bindings and unresolved rows reject the whole plan before any writes.
Unknown optional native controls can stay untouched; all required and history
controls must be bound. All visible native control descriptors, including ignored
optional controls, participate in change detection.

Bindings are `{field_id, occurrence_id}`. File bindings are only `{field_id}`:
the resolver supplies their authority, not an invented answer occurrence. A
resume file label must be `Resume`, `CV`, `Resume/CV`, `Upload resume` or `Upload CV`
(case-insensitive); other document uploads are unsupported. A
history binding adds `history_key`, for example
`{field_id: 'history-company', occurrence_id: 'occ-…', history_key: 'job-1.company'}`.
Its exact answer must equal `approved.saved_history[history_key]`. The DOM adapter
requires history keys for fields in a fieldset labelled `Work history` or
`Employment history`. The parent must mark any other known history fields with
`history_key` too; the helper never guesses history from prose. It never opens
or edits a profile, and refuses profile-saving/editing labels and LinkedIn
profile URLs. The parent must audit the supplied dialog and native event handlers
as application-only: arbitrary page JavaScript cannot be sandboxed by this API.

The trusted narrow adapter has just these operations:

```js
await adapter.observe(); // no expected answers or snapshots as arguments
await adapter.write(command, expectedScope); // only minted commands
adapter.canUpload === true; // explicit native upload capability, not success
```

The DOM adapter additionally exposes `observeGuarded(guard)`: the executor passes
only a fresh guard callback, so asynchronous hashing and each platform selected-asset
read can recheck STOP immediately before native work. Ordinary adapters retain
the zero-argument `observe()` contract. No approved values are passed to readers.

`observe()` must read actual current values, selected options, file evidence and
dialog identity independently. Never return the expected values as observations.
An observation is `{scope, fields}`. Scope contains nonempty `application_id`,
`tab_id`, `dialog_id`, `dialog_label`, `url`, `root_token`, and `kind`:
`easy_apply`, `ats_application`, or `synthetic_application`. Pin actual browser/tab
identity and exact current URL. Only synthetic scope permits file URLs. Scope
must come from trusted current browser/job context, never from the answer bank.

Each field contains `{field_id, label, section, type, required, writable, options,
node_token, constraints, value, valid}`. Options are `{label, value, disabled}`;
non-choice controls have `[]`. `value` is the actual native value (boolean for a
checkbox, number/null for number, selected native value/null for radio).
`constraints` contains the actual native type, min/max/step/pattern/maxLength/accept
attributes. `valid` reflects native validity; invalid readbacks cannot verify.
Stable IDs and node tokens distinguish control replacement from a value change.
The native adapter additionally uses object identity for the root and controls.

`write` receives only `{field_id, kind: 'set_value', value, occurrence_id,
expected_layout}` or `{field_id, kind: 'upload_resume', resume, expected_layout}`.
It must recheck current scope/layout atomically at the actual write boundary,
then set only that identified field. The supplied DOM adapter uses native
property setters and input/change events; it has no generic selector, click,
script-evaluation, key, navigation or profile operation. Only commands minted
during `execute` are accepted by its `write`. Never expose another adapter's
`write` as a generic external command endpoint.

`getRoot()` must return the actual current, visible, connected dialog element,
with an ID and accessible label. `getScope()` returns the actual current
`{application_id, tab_id, url, kind}`. Root identity/label are observed from DOM.
Nested dialogs, hidden native controls, unlabeled fields, unsupported input types,
custom widgets and ambiguous radio groups stop. No page-wide field search or
positional fallback exists. This deliberately conservative reader does not claim
support for every LinkedIn layout. An unsupported Easy Apply layout goes back to
the agent for inspection; computer fallback is never automatically invoked.

## Resume boundary and typed outcomes

`resumeResolver()` must call the parent's current resolver, not return a cached
selection. Execution checks it before any writes and again immediately before
the upload. Missing/failing/changing resolution stops. No older release fallback
is possible. `upload({input, resume})` is an optional **trusted native file chooser
bridge**. It must verify the actual chosen release's ID, canonical path and byte
hash outside browser DOM, choose only that file in the scoped input, and return
the independently verified `{id, path, sha256}`. Do not implement it by echoing
the request or assigning the input's fake path string.

Browser DOM cannot read the source filesystem path or release ID. The native
adapter therefore binds the bridge's verified receipt to the actual selected
File object, then independently computes that File's SHA-256 with Web Crypto.
It rechecks current scope, File identity and actual other field values after
asynchronous hashing. Names/fake paths alone cannot verify an upload. A preexisting
File without this adapter's receipt exposes a digest but null ID/path. Missing
upload bridge, missing Web Crypto, wrong receipt/bytes or changed File always
handoff; no success hash is fabricated. No automatic remote-upload widgets are
authorized by this interface; the parent bridge must refuse such side effects.

Handoffs have `{status: 'handoff', reason, next, written, observed}`. Reasons include
`unsupported_form`, `page_changed`, `approval_required`, `invalid_answer`,
`history_mismatch`, `invalid_plan`, `adapter_unavailable`, `adapter_failed`,
`guard_required`, `guard_refused`, `resume_required`, `resume_resolver_required`,
`resume_resolution_failed`, `resume_changed`, `upload_adapter_unavailable`,
`upload_identity_mismatch`, `upload_verification_unavailable`, and
`verification_failed`. Raw adapter exception text is omitted.

`next: 'agent_review'` is the normal stop. For an unsupported ATS scope only,
`agent_review_allowed_ats_fallback` is a hint to the parent, not authorization.
Invoke the existing [computer fallback](computer-use.md) only after its own DOM
failure, explicit external-host allowlist, Easy Apply exclusion, live/frozen
policy, guard, writer lock and budget checks pass. Never use it for Easy Apply.

## Focused verification and parent browser check

```sh
node --test tests/forms.test.cjs
```

The standard-library Node tests parse the synthetic fixture through a small DOM
double and exercise the same exported adapter, native property setters, File
objects and actual SHA-256 hashing. They also cover no-op writes, reactive resets,
changed scope/options, stale resume resolution, missing upload capability and
wrong file bytes/identity. They do not run a real browser or authenticated site.

Parent manual verification: open `tests/fixtures/application-form.html` in the T3
collaborative preview. The self-contained fixture has no form element, submit
controls, dependencies or network operations; CSP blocks connections and form
actions. Fill `Full name` with `Example Applicant`, experience with `1`, choose
`Foreigner` and sponsorship `Yes`, check background consent, and enter fictional
history `Example Cedar Labs`, `2025-01`, `2025-12`. Inspect actual selected values
in the output. Choose only a synthetic PDF and observe its actual local name/size.
No application is sent. The fixture's output does not claim a release hash.

For a module-level browser smoke, inject the unchanged module source through the
preview's evaluator (do not fetch external scripts). Use `createDOMAdapter` with
this dialog, actual preview URL and `kind: 'synthetic_application'`. Keep one
adapter instance for planning/execution. If native upload capability is absent,
leave `upload` undefined and confirm `upload_adapter_unavailable` before writes;
do not simulate verified upload success. A real bridge plus fresh synthetic
resolver selection is required to test verified upload end to end.

Capture a plan, then check `Changed layout case` before execution: it adds a
required unknown field and an unfamiliar option. Expect `page_changed` or
`unsupported_form` and zero field writes. Replanning against the old approved
rows also must stop. No browser verification was performed by the implementation
sub-agent; the parent owns this manual check and its evidence.

Parent browser verification completed on 2026-10-20 using only the loopback fixture in an incognito collaborative tab. The real DOM adapter independently verified eight native text/number/select/radio/checkbox/history values. With the required upload and no bridge, it returned `upload_adapter_unavailable` before any field write. A separate actual file-chooser selection used synthetic bytes whose browser File SHA-256 matched the local fixture. A changed layout stopped the existing plan with `page_changed`, zero writes and zero field events; replanning old approvals returned `unsupported_form`. The page had no submit controls or external requests. Native bridge identity/hash binding is additionally covered by synthetic Node adapter tests; no authenticated employer browser is claimed.

Verified selected platform assets and matching prefilled values are reused with
independent readback and reactive-reset protection. See [efficiency](efficiency.md)
for the private receipt/telemetry boundary, fresh resolver requirements, bounded
observations and synthetic before/after evidence. A filename alone never enables
reuse. Live LinkedIn selection inspection still requires a runtime adapter.
