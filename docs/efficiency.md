# Safe harness efficiency (issue #55)

Applications remain stopped. This change authorizes no browser login, employer
action or submission. All evidence below is synthetic. The repository supplies
helpers and a concrete runtime boundary, not an authenticated LinkedIn driver.

## Resume and field reuse

`resume_assets.Boundary(root, scope, guard=..., clock=..., read_asset_bytes=...)`
is the Python side of the form adapter's `resumeResolver`, `resumeVerifier` and
`rememberResume` callbacks. The runtime transport binds `resolve()`,
`verify(request)` and `remember(request)` to those callbacks, under the existing
writer. Never call a locking CLI from inside that lock.

`createDOMAdapter({getRoot, getScope, upload, selectedResume})` independently
observes the current selected platform asset through
`selectedResume({scope, field_id})`. It must return null or the exact opaque
`{account_id, asset_id, revision}`. If the platform cannot expose an unambiguous
revision/identity, return null and use verified upload or handoff. Do not derive
an identity from a displayed filename, cached card or default selected control.
This callback is a trusted native boundary which a live integration must supply;
there is no bundled LinkedIn selector implementation claiming to do it.

Private `resumes/platform-assets.json` receipts bind that identity to a release
ID and SHA-256, verification method, verification time and expiry (at most 24h).
`remember` requires actual independently read uploaded/downloaded bytes. The
Boundary's `read_asset_bytes(asset)` must read and verify that exact current
asset, not echo the expected hash or read the source file as platform evidence.
An unmapped preexisting asset can establish a `verified_download` receipt by
calling `resume_assets.remember` with independently downloaded bytes; otherwise
upload or hand off. A changed asset/account/revision, changed release or expired
receipt cannot reuse. The current resolver runs again on every verification and
before use: withdrawn, superseded, overdue, changed-byte or expired releases block
entirely, without reverting to an older release. A valid current release with an
unmapped asset may upload. Missing native upload capability is a typed handoff
before unrelated field writes.

Execution compares current valid field values to approved exact bindings. It
skips matching text/number/boolean/select/radio values and verified attachments,
returns `skipped` and `verified` IDs privately, writes mismatches once and checks
independent readback. Each post-write observation also becomes the next operation's
input; initial + each write's readback + final observation bounds outer reads to
`writes + 2`. Earlier skipped/written values are checked for reactive resets.
Asynchronous asset/hash observations reread current native field values and
recheck input identity/layout. Guards run inside each measured callback, after its
awaited durable span reservation and immediately before every observe, resolver, receipt operation,
verification and write. STOP during telemetry persistence blocks the operation.
Every write and receipt callback also revalidates the exact release ID/path/hash
after reservation, without a nested resolver completion save before the native
call. Final attachment verification and release validation run after all
instrumentation persistence; this also covers local File reuse and receipt
invalidation during earlier completions. Resolver/verifier callbacks must not
add trailing telemetry persistence to these final checks. Their effort remains
inside the enclosing browser/workflow span.
The DOM adapter also rechecks the supplied guard after asynchronous file hashing
and before each native selected-asset callback. No automatic write retry or
rollback is introduced.

Reuse performs zero file-picker, upload or path-entry commands. Fresh local hash
resolution remains mandatory. Exact submitted copies, provenance, hashes and
independent attachment/form review still use the existing preparation/reviewer
flow. A reuse receipt grants no review or submit permission.

## Private span contract and live integration

The existing v1 action ledger remains compatible. V2 spans live separately in
`applications/runs/<run>/spans.json`. Do not publish either ledger. A span has:

- `schema_version=2`; run/application/attempt/action/span/parent/process IDs;
- fixed `phase` and `channel` labels (enumerated by `spans.PHASES/CHANNELS`);
- Singapore-offset start/end wall timestamps, same-process monotonic seconds;
- fixed outcome, coverage, numeric allowlisted counters and optional model usage.

Open spans are durably reserved before crossing the tool boundary. Interruption
leaves duration/end unknown. A new `Recorder` gets a new process instance ID;
no monotonic origin is persisted, compared across restarts or reconstructed from
wall time. Each run's recorded segments remain separate across process restarts.
Telemetry failure sets `Recorder.available=False` (JS `.available` likewise),
never replays the callback, never creates a confirmation and does not convert a
successful authoritative mutation into a failure. Existing v1 action-ledger
failure still retains pending and requires reconciliation as before.

`Runner.execute` wraps actual planning/context/startup, guard/preflight, adapter,
verification, persistence, retry, handoff and checkpoint-export paths. Existing
workspace helpers instrument actual capture, eligibility, answer batching,
review and persistence when called inside that runtime. `spans.LiveAdapter`
always records the actual outer browser boundary. A runtime may implement
`perform(action, boundary)` instead of `__call__(action)` and use
`boundary.call(phase, channel, callback)` for navigation, listing capture, file
picker, upload, observations/readback and other tools. Legacy callable adapters
keep their behavior and only their observed outer boundary is measured.
`LiveAdapter(adapter, guard=...)` requires a fresh authorization callback (`None` succeeds; refusal raises). It
checks after each outer reservation, including URL observation. The boundary
passed to `perform` also guards each nested `call` after its reservation. Guards
run without nested telemetry writes: their effort stays in the enclosing span,
and no guard-span completion save can introduce another STOP window. The runner
rechecks policy/budgets and uses `Hooks.native_guard` (or the ordinary guard when
absent). After `begin_submit`, the workspace hook checks this invocation's exact
owned durable claim, transitive aliases and revalidates prepared bytes, answers, review, eligibility,
policy, writer ownership and STOP. It never recovers or clears an intent to make
another click possible; refusal retains pending uncertainty for reconciliation.
`boundary.correlate(action, actual_attempt_id)` adds a newly prepared attempt;
unknown IDs remain null rather than being inferred.

For JavaScript/browser tools, load `telemetry.js` and construct:

```js
const telemetry = ApplydTelemetry.create({
  context: {run_id, application_id, attempt_id, action_id, parent_id},
  processId: actualRuntimeProcessInstanceId,
  emit: row => privateTransportPersistSpan(row)
});
await ApplydForms.execute(plan, adapter, {
  guard, resumeResolver, resumeVerifier, rememberResume, telemetry
});
```

`privateTransportPersistSpan` calls `Recorder.ingest(row)` through the held writer,
acknowledging the initial open row and its single completion. The transport must
be private and bounded; it must not log values or retry browser operations. The
same ID cannot be replayed or replace a completed measurement. Explicit parent
IDs preserve siblings for concurrent promises; never share an implicit async
stack between concurrent tools. Python and JS use the same exact schema. The
form helper emits actual observation/write/upload/skip counters. A runtime that
can observe repeated uploads or redundant observations supplies those counters
explicitly; absence stays unknown. Operation counters reserve their declared keys
at zero and increment only
when the fresh guard allows invocation of the native callback. A refused write
or upload completes with zero; an invoked failure counts once. Imported
completions may increase declared counters but cannot remove/change keys or
decrease values. Only completed measured boundaries contribute counts, with
`counter_coverage` exposing interrupted reservations even when their saved value
is zero. Actual record-file reads increment their current span without a
separate telemetry write per file. No filename-based repetition heuristic exists.

For provider calls, `Recorder.generation(phase, configuration, fn)` accepts
`fn -> (value, actual_output_tokens_or_None)`. Optional
`output_usage(result)` extracts usage separately when `fn` returns a provider
result directly. Configuration has private
`provider`, `model`, and `configuration` labels. JS `telemetry.call` accepts
`{model: configuration, outputUsage: result => actualOutputTokens(result)}`.
`generation_seconds` times only the actual provider callback, after reservation
and guards and before tuple unpacking/usage extraction. The inclusive span still
records reservation, guard and extraction effort (its own completion save falls
outside its interval). Validation requires the generation interval to fit inside
the inclusive model span; equality is not required. Only actual output-token
usage and that same generation's measured interval produce tokens/second. Missing
usage remains null; input tokens, model names,
service-tier names and advertised Fast mode do not measure output speed.
Configuration labels remain private; aggregate reports count configurations.

Unwrapped manual browser/model/tool calls and all historical runs without spans
remain uninstrumented. There is no live validation, model performance claim or
claim that the wrapper supplies a missing driver. A separately resumed authorized
run and a runtime implementing these boundaries are required for live evidence.

## Accounting and reporting

`workspace.py metrics` adds aggregate `spans`; JSON weekly reports also include
period-selected aggregate spans. `spans.summarize(records, spans, start=..., end=...)`
is pure and emits only fixed labels and numbers/null. Selection uses aware wall
start timestamps in `[start,end)` and never clips/reconstructs a duration from a
partially intersecting span. V1 action metrics are unchanged.

Phase/channel medians use midpoint for even samples. P95 uses nearest rank:
`sorted_values[ceil(.95*n)-1]`. Every row includes sample count. Anonymous per-process phase summaries expose the same quantiles without process
IDs. Phase samples are
inclusive observations and are never added to their parent's totals.

Critical-path measured active time is the union of measured intervals after
subtracting explicit user/blocked waits from each inclusive ancestor along the
recorded dependency chain, including ancestors in other processes. An enclosing
Python workflow cannot count a browser child's blocking wait as active work.
Independently measured concurrent siblings remain work, including explicit async
siblings in the same process. Overlapping waits/fragments are unioned, never
added twice. Unknown waits or orphaned dependencies leave active time unknown.
Observed descendant waits still apply to a selected inclusive parent when the
wait starts outside the report period. Concurrent processes cannot inflate this
denominator. Summed process effort is the sum of interval unions within
each process, excluding nested samples with a measured same-process ancestor.
This inclusive process effort includes explicit waits and orchestration, and may
exceed critical-path active time; it is not CPU time. Inclusive orchestration spans
are not claimed as exclusive overhead; `exclusive_channels` subtracts the union
of all recorded direct children, including children starting outside the report
period of a selected inclusive parent. Missing/interrupted child coverage makes
that exclusive total unknown even when the child is excluded by the period.
Per-channel values are independent observations, not additive CPU
allocations when differently labeled siblings overlap. Failure/retry time uses interval unions, not nested sums.

Durations always come from monotonic clocks. Cross-process overlap placement
uses wall starts plus those measured lengths, with explicit
`clock_alignment=wall_placement_with_monotonic_lengths`. Wall-end agreement within
10ms is required. Clock discontinuity makes active/process/exclusive aggregate
durations unknown; unknown waits also make active time unknown. Aligned wall
clocks are an explicit placement assumption, never proof of synchronized processes.
Wall-run hours sum each run's first-to-last recorded workflow envelope (including
gaps); an interrupted workflow makes that denominator unknown. Each execute
call's workflow envelope includes its final bounded checkpoint export, including
STOP and ordinary failure paths. Reservation/completion acquire the writer;
actions retain their own writer boundaries. Export failure remains a separate
`export_outcome`, never replacing an authoritative action result or replaying it.
Time to first confirmation is explicitly a wall timeline delta, not reconstructed process effort.

Unique confirmations use the existing evidence-backed transitive identity groups
and earliest confirmation across all history. Later aliases/retries do not earn
credit. Throughput counts only first confirmations belonging to a recorded run's
wall envelope, with separate `wall_throughput_confirmations` and `throughput_confirmations`,
wall-run seconds and measured active seconds exposed. The active numerator
requires an actual completed workflow segment covering that first confirmation;
an interrupted segment never supplies a wall-run denominator. A legacy confirmation remains counted in overall volume
but cannot be attributed free effort. Missing/open phases, usage and counters
remain unknown; `recorded_intervals_only` coverage never asserts complete lifetime
coverage. Telemetry cannot promote an unsupported submitted-status claim.

## Other redundant work

`discovery.discover_saved` is the concrete discover-adapter helper. It requests
at most 20 visible Saved cards by default (hard maximum 100), screens stable
identities/transitive aliases and cheap current/frozen header exclusions before
opening details. Confirmed/uncertain aliases, including empty intents, are
excluded; existing queued identities stay with the queue; repeated cards are
collapsed. Ambiguous/incomplete headers still require full listing capture and
eligibility. Each open rechecks fresh guard, identities and policies after the
measured screen's completion. This final full decision suppresses nested
telemetry writes, so no completion persistence intervenes before navigation.
The adapter must itself bound observation, open only the card's listing, and call existing
scope/freshness/capture gates. No profile navigation or broad search is added.

The runner queue uses `workspace.planning_snapshot` only while holding the writer
inside one planning callback. Answer requeue receives that same record snapshot.
Record mutations invalidate it; copies cannot mutate the cache. The context ends
before browser boundaries. STOP, ownership, policy, answer expiry/consent scope,
resume resolution, quota, confirmation and submit intent are checked fresh at
normal action boundaries. Never retain this snapshot across an action.

Continue using `prepare_answers` with the entire current step's question list:
one bank read resolves all exact scoped aliases/authorized defaults and queues
only unresolved rows. Existing consent/application/country/type/options/units,
expiry and provenance rules remain authoritative. Ordinary previously authorized
handling does not create another consent question; unknown material declarations
still do. No fuzzy answer inference or broad consent default was added.

Runner adapter mutations can use existing deferred-export state.
`Hooks.checkpoint` in the workspace integration repairs dirty CSV/dashboard once
at bounded execute completion/stop; `Runner.export_outcome` exposes its separate
result. The existing `work_guard` submit-recovery path can also trigger a rebuild
inside confirmation recording; the connected fixture counts this additional
export rather than claiming all derived work is deferred. A process
interruption retains the durable dirty marker for existing export-only recovery.
The final checkpoint does not delay authoritative confirmation or submit-intent
persistence. CLI remains export-recoverable and explicit `--defer-export` continues to defer its final rebuild.

## Reproducible synthetic baseline and verification

```sh
python3 scripts/synthetic_baseline.py
python3 scripts/synthetic_baseline.py --measure-overhead
python3 -m unittest discover -s tests -v
node --test tests/*.test.cjs
python3 scripts/check_publication.py
```

The deterministic output is checked in as `synthetic-baseline.json` and compared
by regression. Both schedules initialize identical temporary workspaces with the
same four native fields (one mismatch), saved answers, released synthetic resume,
independent synthetic platform bytes and a real `verified_download` receipt.
No model/provider calls occur. The private temporary workspace is discarded.

The connected adapter runs `Runner.execute` through actual answer preparation,
frozen submitted copies/hashes, form execution, independent review, fresh
preflight and URL observation, durable submit intent, a local synthetic
confirmation event, evidence verification and final checkpoint. A local Node RPC
transport calls the real guarded Python resume `Boundary` and `Recorder.ingest`.
Every imported JS reservation/completion has its actual Python browser parent and
application/attempt/action correlation. There is no authenticated driver or live
submission. Regression faults prove both schedules refuse failed field readback
and an independent review mismatch; a corrupt receipt triggers verified upload.

The reference freezes the original unconditional form write/observation schedule,
uncached planning and immediate mutation exports. It uses the same adapter input,
current guards/resolver, exact layout/readback and reactive-reset checks, actual
platform-byte receipt verification, independent review and submit gates. It is a
safe executable reference schedule, not a historical implementation timing claim.
Success is derived from checks and durable evidence; no branch declares a form
verified without checking its observed values and attachment.

Across the entire connected workflow, field writes change 3 to 1; uploads/path
entries 1 to 0; observations 11 to 4 (including the independent review read);
record reads 149 to 103; exports 5 to 2. The after run includes one inherited
submit-recovery export and one bounded checkpoint export. Two matching non-file
fields are skipped; verified attachment reuse performs no upload/path entry.
These are actual helper/adapter invocation counts, not separate illustrative loops.
Both schedules include fresh authorization after durable reservations; the final
guards have no nested span writes and retain their time in the enclosing span.

Injected costs are observation 0.2s, field write 0.3s, upload/path 0.9s, record read
0.01s, export 0.1s and durable span save 0.001s. Instrumented workflow elapsed
latency is 6.746s before and 2.982s after, with one sample each. Recorded workflow
wall/active denominators are 6.745s and 2.981s; the final envelope's own completion
save adds 0.001s outside its measured interval. Each run has one evidence-backed
**synthetic** confirmation, producing fixture rates of 533.728688 and 1207.648440
per wall/active hour. First-confirmation latency is 6.407s and 2.667s. These fake
costs, single-sample quantiles and rates make no claim about live performance.

Paired executions disable telemetry only, preserving all guards, verification and
workflow inputs. Elapsed latency is then 6.390s and 2.730s. Thus injected durable
telemetry overhead is 0.356s (356 saves) and 0.252s (252 saves), including both
Python spans and ingested JS rows. Uninstrumented pairs do not claim measured
active/wall span denominators. Separate fake-clock confirmation/cross-post/retry
and partial-coverage tests retain zero/unknown and first-confirmation coverage.

`--measure-overhead` additionally measures 20 paired identical local arithmetic
callbacks with/without real durable span persistence using host `perf_counter`.
It prints only aggregate median/p95 and sample counts; no raw traces or paths.
These host-dependent values are not assertions or a live speedup. Live throughput,
upload behavior and model speed remain unvalidated.
