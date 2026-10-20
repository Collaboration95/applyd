# Computer-use fallback

`scripts/computer_fallback.py` supplies a standard-library-only, adapter-driven
session boundary for issue #24. It has no browser, desktop, network or CLI driver.
Settings/schema integration is included; the parent owns CLI dispatch, the live
adapter and manual browser QA. The module does not invent adapters or screenshots.

## Parent settings integration

The optional schema-1 key is implemented in settings validation and the example:

```json
"computer_use": {"enabled": false, "step_budget": 6}
```

`enabled` must be a boolean; `step_budget` must be a non-negative integer, excluding
booleans. Reject unknown nested keys. A missing key disables fallback; omitted
nested fields default to `false` and `6`. Zero budget permits no actions. There is
no unlimited/null budget. Persist these values in the existing frozen run settings.

Fallback also requires `mode: "apply"`, `easy_apply_only: false`, and an explicit
non-empty `external_hosts.allow`. Host rules match the named host and its
subdomains, with deny taking precedence. LinkedIn and all its subdomains are
always rejected, even when allowlisted. A browser-observed Easy Apply form is
rejected on every observation, including when hosted on another allowed domain.
Malformed/missing host policy fails closed. URLs must be HTTP(S), with no userinfo.

Start the run with `python3 scripts/workspace.py run-start --id <run-id>`;
`settings.run_start(root, run_id, aware_now)` freezes the full settings, including
`computer_use`. Old snapshots retain their exact raw settings/hash and are not
rewritten to insert new defaults. A missing frozen `computer_use` disables fallback
even after current settings enable it. `FROZEN_V1_REQUIRED` remains unchanged.

`effective_policy(current, frozen, initial_url)` validates both settings objects
and returns their intersection: both must enable fallback and apply mode; the
smaller step budget wins; both must explicitly allow the observed host and neither
may deny it or require Easy Apply only. The result also pins the exact normalized
origin. All denials are retained; allowlists are never unioned. Inputs are not
modified. Invalid settings/origins raise `settings.SettingsError`.

## Public API

Import `Action`, `Observation`, `Outcome`, `Result`, `Adapter`, `effective_policy`,
`run_workspace_session` and `run_session`
from `computer_fallback` with the scripts directory on the import path.

```python
def guard(phase):
    # Call the parent's live work_guard plus eligibility/answer/ownership checks.
    # Raise or return False to stop. phase is "observe" or "action".
    work_guard(workspace_root, run_id, aware_now(), "computer-use")
    check_application_and_writer()
    return True

result = run_workspace_session(
    workspace_root, run_id, session_id,
    guard=guard,
    adapter=adapter,
    initial_tab_id="opaque-browser-tab-id",
    initial_url="https://jobs.example.test/application",
    failure_kind="custom_widget",
    dom_failed=True,
    actions=[Action("select_option", "language-widget", "Python")],
)
```

This is an API example with a fictional host, not a runnable browser driver.
`run_workspace_session(root, run_id, session_id, *, guard, clock=None, **arguments)`
loads only this workspace's current settings and verified existing run, initially
and before every adapter call. It enforces both elapsed deadlines from the original
start; current changes can disable fallback, narrow scope or shrink the budget
during the session. `clock()` is optional and must return an aware datetime.
Failed loads, invalid clocks and expired deadlines return durable
`failed / policy_refresh_refused` without exposing exception text.

`arguments` are the `run_session` keyword arguments shown above, plus optional
`resume_resolver`/`resolved_resume`. Do not pass `policy` or `refresh_policy` to
the workspace helper. `run_session(..., policy=effective_policy(...), guard=guard)`
is the lower-level API for a parent that already owns policy refresh. Its optional
`refresh_policy()` callback must return a complete effective policy; prefer the
workspace helper so live deadlines and current settings cannot be overlooked.
`run_id` and `session_id` are 1–128 ASCII letters/digits/underscores/hyphens,
beginning with a letter or digit. `initial_tab_id` is an opaque, non-empty string;
the adapter must preserve its exact browser identity, including browser/window
identity where needed to avoid collisions. `initial_url` comes from the parent's
observed current tab. Scope is pinned to the initial tab and normalized origin
(scheme, exact hostname, effective port). It cannot drift to another allowed
subdomain. URL path changes do not authorize navigation actions.

`dom_failed` must be exactly `True`. Record the actual DOM failure in the parent's
private run context before invoking fallback. Only these combinations are valid:

| `failure_kind` | `Action.kind` | `Action.value` |
|---|---|---|
| `canvas`, `custom_widget` | `select_option`, `set_widget_value` | approved string value |
| `native_file_picker` | `upload_chosen_file` | `None` |

`Action.target` is the adapter's non-empty identifier for a known safe widget.
There is no arbitrary click, coordinate, key, script, navigation, submit or send
action. An invalid action anywhere in the list rejects the whole plan before
interaction. `actions` must be a non-empty list/tuple, not an unbounded iterator.

For file selection, pass `resume_resolver=lambda: resolve_current_application_resume()`.
The parent callback must invoke `python3 scripts/resolve_resume.py --scope <scope>
--purpose application` or its existing resolver API immediately before upload.
The module invokes the callback after the before observation and action guard,
checks the returned path, records that exact selection, and guards again before
`perform`. A failed current resolution returns `failed / resume_resolution_failed`;
it never falls back to `resolved_resume` or an older release. The low-level
`resolved_resume` option requires the parent to guarantee the same immediate
current-resolution discipline. The module accepts only an existing file
inside the selected workspace and passes its canonical `Path` to the adapter.
No resume bytes are read by this module. Paths cannot come from the action value,
listing, widget, screenshot or browser-provided text. The parent remains
responsible for resolver freshness, provenance and immutable submitted copies.

`Result.outcome` is an `Outcome` string enum: `disabled`, `off_scope`,
`budget_exhausted`, `screenshot_missing`, `completed` or `failed`.
`result.outcome.value` is suitable for JSON/CLI output. `reason` is a stable
content-free code; `step_count` is the durable attempted-action count;
`record_ref` is a workspace-relative private audit reference. `completed` means
the supplied widget plan finished with evidence; it never means an application
was submitted or that a selected value was semantically correct. Verify the
resulting widget value through the parent browser tools before continuing.

The injected `guard(phase)` is required and must return exactly `True`. It checks
the parent's live safety/kill switch, eligibility, answer freshness and ownership
before **every** `observe()` and `perform()` call, including after an action raises.
Missing guards return `failed / guard_required`; false/None returns or exceptions
produce `failed / guard_refused`. No adapter call crosses a refused boundary.
A stop after an action leaves `after: null` and preserves the spent step; do not
turn a fallback outcome into submission authorization. Missing browser/auth
capability must end with a typed failure and must not install tools, authenticate,
or create substitute screenshots automatically. Passing `adapter=None` produces
`failed / adapter_unavailable` with no interaction.

## Adapter contract

Implement just two methods; the module makes no browser or OS calls itself:

```python
def observe(self) -> Observation:
    # Inspect the actual CURRENT tab/focus and capture an actual screenshot.
    return Observation(tab_id, current_url, private_image_reference, easy_apply=False)

def perform(self, action: Action, *, tab_id: str, origin: str,
            resume_path: Path | None) -> None:
    # Recheck browser focus/scope and safety gates immediately before acting.
    # Dispatch only the permitted widget operation to an audited safe target.
    ...
```

`observe()` must not switch tabs or navigate to manufacture the expected scope.
It must detect Easy Apply accurately; the default is not permission to skip
detection. Use the same exact tab token across observations and `perform()`.
The module checks current scope before and after every attempted operation,
including when `perform()` raises. Since external browser state can change
between tool calls, the adapter must also check the supplied scope atomically at
the actual interaction boundary. It must stop on a new tab, host/origin change,
login wall, captcha, restriction or uncertain focus. It must not try to navigate
back or refocus another tab to recover automatically.

Only audited, non-submitting widget targets are permitted. Never map these
operations to submit/continue/send controls, links, Enter, generic key sequences,
arbitrary scripts or browser navigation. File selection means choosing only the
parent-resolved file in the existing picker/input. It cannot upload other files,
inspect file-system contents, send an application, or trigger an automatic remote
upload. Refuse widgets with such side effects. The adapter is a trusted boundary:
action names alone cannot prove a widget's JavaScript has no side effects. No
real employer/browser safety is claimed from fake-adapter tests.

`observe()` supplies screenshot references to existing, non-empty `.png`, `.jpg`,
`.jpeg` or `.webp` files **under `applications/runs/<run_id>/`**. Absolute paths or
workspace-relative references are accepted; canonical private relative references
are saved. Public paths, other runs, missing/empty files and symlink escapes are
rejected. The module does not take, copy, decode or fabricate screenshots; the
adapter is responsible for actual image content and capture timing. Use a new
reference for every capture; an identical before/after reference is rejected.

A missing before image prevents the action. A missing after image produces
`screenshot_missing`, retains the spent step and records `after.screenshot: null`;
it never invents missing evidence. If `observe()` raises after an action, the
outcome is `failed` with `after: null`. Preserve this uncertainty, stop the
session and reconcile via the parent; do not retry the action. Raw adapter
exception text is never serialized.

## Persistence and restart

The private audit is
`applications/runs/<run_id>/computer-use/<session_id>.json`. It contains the pinned
scope, failure kind, frozen step budget, count, terminal outcome/reason, timestamps
with `+08:00`, exact actions, parent-selected resume reference when used, and
before/after observations and screenshot references. It does not modify
application records, count applications, export this data to the dashboard or
include it in public artifacts.

Each action is durably reserved and logged as `pending` **before** invoking the
adapter; failed actions consume budget. Writes use private temporary files and
atomic replacement. A per-session exclusive lock rejects concurrent writers.
An exclusive `applications/computer-use.lock` also serializes fallback sessions
across all run/session IDs in this workspace. An existing lock returns
`failed / browser_busy` and is never automatically removed. Before every adapter
boundary, an existing `applications/browser-claim.json` (even a dangling symlink)
returns `failed / pending_submission_claim`; the claim is never overwritten.

The parent must make other browser writers honor `applications/computer-use.lock`.
For Submit, check it before publishing the existing browser claim **and again
after publishing the claim, before any click**. This pairs with fallback's
claim checks to close the concurrent-acquisition window. Never remove another
writer's lock, reinterpret a pending submit claim as fallback ownership, or reuse
fallback as submission recovery. Keep one browser writer per session.

CLI glue remains parent work: dispatch to `run_workspace_session`, pass the live
guard and adapter, and serialize `outcome.value`, `reason`, `step_count`, and
`record_ref`. For absent capability pass `adapter=None`. Do not expose action JSON
as a generic browser command or accept browser-provided resume paths. The only
documented commands here are existing run-start/resolver commands and synthetic
tests; there is no standalone computer-use CLI/browser driver in this module.

Use the same durable session ID when restarting the same fallback episode;
never generate a new ID to bypass a budget or failure. Returning to a terminal
session returns its original result without calling the adapter, even if settings
or the requested plan have changed. Rebinding the session to another tab, origin
or failure category fails closed without altering the original audit.

An interrupted, nonterminal record is finalized as `failed / interrupted_session`
without replay, preserving its original count and pending action evidence.
A process killed without cleanup can leave a lock: `failed / session_busy` is a
blocker. The parent must reconcile ownership and the recorded action manually
before removing a stale lock; automatic lock removal/retry is unsafe. Corrupt or
incompatible records return `failed / invalid_session_record` and remain intact.
For session-busy/corrupt records `step_count` is `None` because it cannot be trusted;
these failure returns do not overwrite another writer's or damaged evidence.
The initial `step_budget` remains the session ceiling; `effective_step_budget`
records a lower limit observed during live refresh. An already reserved step can
be stopped by a guard, policy edit, or resolver failure before `perform`; it still
counts and is logged as `blocked`. A smaller live budget never grants extra steps
and never erases previously reserved ones.
Invalid IDs/workspace paths, symlinked storage or disk errors raise
`ValueError`/`OSError`; the parent CLI must map these to a typed `failed` blocker
without printing private paths or exception contents. Never act after a storage
failure. The workspace and adapter are trusted local components, not a sandbox
against a malicious local process.

## Synthetic verification and parent browser prerequisite

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_computer_fallback.py -v
```

Tests also cover settings defaults/schema, exact raw legacy snapshot hashes,
current/frozen intersections, live deadlines, guard refusals, shared locks and
immediate current resume resolution. Tests use temporary synthetic workspaces
and fixture-labelled 1x1 PNGs supplied
by `FakeAdapter`; they are not real screenshots or proof of browser execution.
There are no browser, OS automation, private-workspace reads or external actions
in these tests.

The parent must manually open `tests/fixtures/fallback-form.html` with the
collaborative preview. The self-contained page has a custom radio widget, canvas
selector and native file input; no form, submit control, links, dependencies or
network operations. CSP also disables connections and form actions. Check that
choosing Python updates `Selected option`, selecting canvas value 2 updates both
its output and `aria-valuenow`, and choosing a **synthetic** PDF shows its name
without sending anything. Capture genuine before/after evidence privately if
exercising an adapter. No browser QA was performed by this implementation agent.

Opening the fixture directly as `file://` is for manual widget QA only; fallback
correctly refuses file URLs. An end-to-end adapter check needs an available
browser/native-picker capability and the fixture served from a loopback HTTP
origin explicitly allowed by synthetic policy (for example, `localhost`), with
real private capture references. Keep the exact initial tab/origin and never
allowlist a live employer just to test. If preview/native-picker capability is
unavailable, record that blocker and retain the passing synthetic test evidence.

Parent integration: `workspace.fallback_session` supplies the live workspace guard, exact current resume resolver and current/frozen settings. The `computer-use --id ... --run-id ... --session-id ... --plan ...` CLI validates and journals a supplied plan but returns `adapter_unavailable` when execution needs an adapter; it never fabricates an action or screenshot. A runtime adapter can call the same function directly under `workspace.writer`. Submit checks the shared fallback lock before and after reserving its own claim.

Manual browser verification on 2026-10-20 used the collaborative browser with the fixture served only on loopback in an incognito tab: Python selected, canvas value 2 matched its accessible value, the actual file chooser selected a synthetic PDF, and the page had no submit controls or external resource requests. This checked the real fixture widgets; policy, durable budget and failure behavior were verified with synthetic adapters.
