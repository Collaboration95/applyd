# Deterministic listing eligibility

`scripts/eligibility.py` exposes `evaluate(listing, policy, facts=None)`. It uses
only its arguments and the standard library. It does not load a workspace, read
profiles, consult the clock, call a model, use the network, or write records.
Inputs are not mutated; the result is a new JSON-compatible object.

```python
from eligibility import evaluate

decision = evaluate(
    {"location": "Example City", "employment_type": "Full-time",
     "job_description": "3+ years of experience. We cannot sponsor visas."},
    {"max_required_years": 3,
     "role_rules": {"allowed_locations": ["Example City"],
                    "allowed_employment_types": ["Full-time", "Internship"]}},
    {"sponsorship_required": {"value": True, "confirmed": True, "current": True}},
)
# {"decision": "excluded", "reason_codes": ["sponsorship_unavailable"],
#  "used_facts": {"sponsorship_required":
#                 {"value": True, "confirmed": True, "current": True}}}
```

`decision` is `eligible`, `excluded`, or `needs_review`. A proven exclusion wins
over a review condition. `reason_codes` is a sorted, unique string array retaining
both kinds of reason; an eligible result has an empty array. `used_facts` maps
only consumed, usable fact IDs to their boolean value and confirmation/current
flags. It omits unrelated facts, source text, and unusable values. This result is
private application data, not public telemetry. `eligible` means no blocker was
found by these rules; it is not proof of all qualifications or permission to submit.

Malformed listing/policy/facts containers, unsupported role-rule keys and invalid
role-rule values raise `EligibilityError` (`ValueError`). Settings validation
continues to raise `settings.SettingsError` (`ValueError`). Both errors must block
execution; neither is an eligible decision.

## Policy and listing inputs

`policy` is the existing settings dictionary or a partial settings object.
`settings.eligibility(listing, policy)` always validates settings and evaluates
company/title blocklists, external-application/host policy, application URLs and
explicit numeric `required_years`. Its rejection codes and gate precedence are
preserved. See [settings.md](settings.md). Modes, quotas, resume freshness and
submission intent remain separate execution gates.

The evaluator interprets these existing settings keys:

- `max_required_years`: non-negative integer; `null` or omission disables the
  experience limit. It is a listing minimum threshold, not a candidate's years.
- `role_rules.allowed_locations`, `role_rules.excluded_locations`: arrays of
  non-empty location labels, `null`, or omitted.
- `role_rules.allowed_employment_types`, `role_rules.excluded_employment_types`:
  arrays of non-empty employment-type labels, `null`, or omitted.

Empty/null/omitted arrays impose no restriction. Denial wins over allowance.
Labels use Unicode normalization, case folding and collapsed whitespace, then
exact equality. There is no country default, substring match, geocoding, implicit
remote-location permission or inferred employment preference. List every accepted
label explicitly. With an active rule, a missing value needs review; a value
outside a non-empty allowlist is excluded. Unmatched alternatives separated by
`or`, `/`, `;` or `|` need review. A composite label can be allowed exactly.
Unknown `role_rules` keys fail rather than silently dropping a policy restriction.
Existing settings snapshots/hashes do not need a schema change.

Listing fields are `job_description` (full captured text), `location`, and
`employment_type`, plus the header fields documented by settings. A missing/blank
description needs review. `required_years`, when supplied, must already be a known
hard minimum; preferences/guessed numbers must never be put in that field. Header
fields remain usable before a full description is obtained. An explicit header
exclusion still wins over a missing description.

## Experience and wording boundaries

Experience text is interpreted only when `max_required_years` is configured.
Explicit `minimum`, `at least`, `must`, `required`, `mandatory`, and `essential`
wording establishes a lower bound. `3+ years` establishes 3, not 4. Numeric ranges
such as `3–5 years` and `4 to 6 years` use their lower bound, never their upper
bound. With a limit of 3, `3+` and `3–5` pass, while a hard 4 fails.

Preferences such as `4 years preferred`, `desirable`, `ideally`, `bonus`, and
`nice to have` do not establish a hard minimum. `not required`/`do not require`
negations do not create an exclusion. `not less than` and `no less than` remain
inclusive hard minima. Requirements/preferred-qualification headings carry
across following bullets; other recognized headings reset that context.

Bare `4 years of experience`, unsupported word numbers, approximate wording,
upper-bound wording, reversed ranges, alternatives/equivalence, conflicting
must/preferred language, and multiple year mentions within a clause need review.
Company age, degree duration and technology names are not professional years.
Separate clauses retain independent requirements; a clear hard exclusion in one
still excludes when another clause is ambiguous. This is a bounded text parser,
not general language understanding; unfamiliar legal/language/credit wording
must be reviewed by the parent rather than assumed to be covered.

## Current confirmed facts

Each supported fact must have this normalized shape:

```json
{"value": true, "confirmed": true, "current": true}
```

The three interpreted IDs are `sponsorship_required`,
`legally_authorized_to_work`, and `credit_bearing`. Values and flags must be
literal booleans. Bare booleans, null, missing records, stale/unconfirmed flags,
and malformed values are unknown. Extra fact metadata is ignored and not echoed.

The parent must resolve source provenance, application/jurisdiction scope,
confirmation and expiry through the existing answer/context layer before setting
these flags, and must repeat freshness checks before execution. This pure helper
cannot verify timestamp expiry or scope by itself. Do not mark an old/raw answer
current merely to satisfy this interface.

Explicit inability to sponsor excludes **only** when a usable
`sponsorship_required` is true. False passes that restriction; missing/unknown
needs review. Conditional/contradictory sponsorship statements need review even
with facts. Event/conference sponsorship is not employment sponsorship. `No
sponsorship required` does not prove that sponsorship is unavailable.

An explicit credit-bearing requirement or legal right-to-work requirement consumes
its own fact. True satisfies that requirement; false excludes; unknown needs
review. Citizenship, residency, clearance, school endorsement, natural-language
qualifications and unfamiliar legal categories need scoped interpretation. The
helper does not map them to the three booleans. A preferred language is not a hard
gate. Authorization and sponsorship remain separate: address, Student Pass,
degrees, residency labels and technical skills never establish either fact.

## Reason codes and parent integration

Existing settings exclusions are `skipped:external_apply`, `skipped:blocklist`,
`skipped:invalid_application_url`, `skipped:external_host`, and
`skipped:required_years`. Parsed hard experience exclusions also use
`skipped:required_years`.

Additional exclusions are `location_excluded`, `location_not_allowed`,
`employment_type_excluded`, `employment_type_not_allowed`,
`sponsorship_unavailable`, `credit_bearing_requirement_unmet`, and
`work_authorization_requirement_unmet`.

Review codes are `description_missing`, `location_unknown`, `location_ambiguous`,
`employment_type_unknown`, `employment_type_ambiguous`, `experience_ambiguous`,
`sponsorship_ambiguous`, `sponsorship_required_unknown`,
`credit_bearing_requirement_unknown`, `credit_requirement_ambiguous`,
`work_authorization_requirement_unknown`, `legal_requirement_ambiguous`, and
`language_requirement_ambiguous`.

The parent owns CLI/capture/requeue/runner integration and publication allowlists.
Pass full captured listing text on retries, evaluate both current and frozen
settings when applicable, and persist the actual decision, codes and consumed
facts privately. Requeue only an `eligible` result; a decision dictionary is always
truthy, so never use `bool(evaluate(...))` as the callback. Neither review nor
exclusion is a submission. Preserve existing confirmed/uncertain attempt rules
and all submit-time checks. This helper adds no live actions or authorizations.

Verification (synthetic fixtures only):

```sh
python3 -m unittest discover -s tests -p test_eligibility.py -v
python3 -m unittest discover -s tests -p test_settings.py -v
```

The workspace now evaluates this policy at capture, preparation/requeue and preflight. Explicit exclusions are saved as `skipped`; ambiguous full listings remain `needs_review` with their reason codes. `workspace.py eligibility --id ...` rechecks an existing captured record without a model. Before execution the saved description hash, current policy and original frozen run policy are all checked again. No original listing or prepared evidence is rewritten.

`confirmed_facts(bank, listing, now)` uses exact saved boolean IDs, current confirmation/expiry and exact matching scope from the answer core. Legal and sponsorship facts additionally require an explicit matching country; a location string or another profile field does not supply that country. Missing, conflicting, stale or differently scoped facts stay unknown. The helper never converts free-form strings such as “Yes” into legal facts.
