"""Durable, reference-only action accounting. Caller holds the workspace writer."""
import math


DEFAULT_POLICY = {"max_actions": 100, "max_retries": 3,
                  "provider_token_cap": None, "provider_cost_cap": None}


def _number(value, *, integer=False):
    numeric = type(value) is int if integer else type(value) in (int, float)
    return numeric and value >= 0 and (type(value) is int or math.isfinite(value))


def validate_policy(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_POLICY):
        raise ValueError("work_budget must contain only max_actions/max_retries/provider_token_cap/provider_cost_cap")
    result = {**DEFAULT_POLICY, **raw}
    for key, value in result.items():
        if value is None and key.startswith("provider_"):
            continue
        if not _number(value, integer=key != "provider_cost_cap"):
            raise ValueError("work_budget limits must be non-negative finite numbers; only provider caps accept null")
    return result


def validate_usage(raw):
    """Per-call measured deltas only. Absence/partial metadata stays unknown."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"tokens", "cost"}:
        raise ValueError("Provider usage must contain measured tokens/cost only")
    result = {"tokens": None, "cost": None, **raw}
    for key, value in result.items():
        if value is not None and not _number(value, integer=key == "tokens"):
            raise ValueError("Provider usage must be non-negative finite numeric deltas")
    return result


def retry_limit(root, record):
    """Current/frozen/saved minimum for EVERY parent retry-policy call site."""
    import settings
    if not isinstance(record, dict):
        raise ValueError("Retry record must be an object")
    policies = [settings.load_settings(root)]
    # Confirmed/outcome/uncertain history cannot execute another retry. Its
    # legacy run metadata must not block independent work or require migration.
    historical = (bool(record.get("submissions") or record.get("submitted_at"))
                  or record.get("submission_intent") is not None
                  or record.get("status") in {"submitted", "confirmed", "submission_uncertain", "acknowledged", "assessment", "interview", "rejected", "withdrawn", "offer"})
    run_id = None if historical else settings._record_run(record)
    if run_id is not None:
        policies.append(settings.validate_settings(settings.load_run(root, run_id)["settings"]))
    limits = [p["work_budget"]["max_retries"] for p in policies]
    if "retry_budget" in record:
        if not _number(record["retry_budget"], integer=True):
            raise ValueError("Invalid saved retry budget; preserve for review")
        limits.append(record["retry_budget"])
    return min(limits)


class BudgetStop(ValueError):
    """Static reason suitable for a short typed handoff."""


def reference(run_id):
    return f"applications/runs/{run_id}/budget.json"


def _path(root, run_id):
    import settings
    root = settings._root(root)
    settings._run_path(root, run_id)
    return settings._path(root, reference(run_id))


def _totals(actions):
    totals = {}
    for key in ("tokens", "cost"):
        values = [(a["usage"] or {}).get(key) for a in actions]
        totals[key] = sum(values) if values and all(v is not None for v in values) else None
        if totals[key] is not None and not _number(totals[key], integer=key == "tokens"):
            raise ValueError("Provider usage total is outside the finite numeric range")
    return totals


def load(root, run_id, sequence):
    import settings
    path = _path(root, run_id)
    if not path.exists():
        if path.is_symlink() or sequence:
            raise BudgetStop("budget_state_missing")
        state = {"schema_version": 1, "run_id": run_id, "actions": [],
                 "provider_usage": {"tokens": None, "cost": None}}
        save(root, run_id, state)
        return state
    state = settings._read(path, "budget.json")
    if (not isinstance(state, dict) or set(state) != {"schema_version", "run_id", "actions", "provider_usage"}
            or type(state["schema_version"]) is not int or state["schema_version"] != 1
            or state["run_id"] != run_id or not isinstance(state["actions"], list)):
        raise ValueError("Invalid budget state; preserve for reconciliation")
    previous = 0
    for action in state["actions"]:
        if (not isinstance(action, dict) or set(action) != {"sequence", "usage"}
                or type(action["sequence"]) is not int or not previous < action["sequence"] <= sequence):
            raise ValueError("Invalid budget reservation; preserve for reconciliation")
        validate_usage(action["usage"])
        previous = action["sequence"]
    if state["provider_usage"] != _totals(state["actions"]):
        raise ValueError("Invalid provider totals; preserve for reconciliation")
    validate_usage(state["provider_usage"])
    return state


def save(root, run_id, state):
    from workspace import atomic_json
    state["provider_usage"] = _totals(state["actions"])
    atomic_json(_path(root, run_id), state)


def check(state, current, frozen, *, reserved_sequence=None):
    """Check observed consumption, never estimate the next provider call's cost."""
    actions = state["actions"]
    if reserved_sequence is not None:
        if not actions or actions[-1]["sequence"] != reserved_sequence:
            raise ValueError("Missing current budget reservation")
        actions = actions[:-1]  # Rechecking permission for this already reserved call.
    usage = _totals(actions)
    policies = [p["work_budget"] for p in (current, frozen)]
    if len(actions) >= min(p["max_actions"] for p in policies):
        raise BudgetStop("action_budget_reached")
    for field, metric in (("provider_token_cap", "tokens"), ("provider_cost_cap", "cost")):
        caps = [p[field] for p in policies if p[field] is not None]
        if not caps:
            continue
        measured = usage[metric]
        # No calls yet is distinct from an unmeasured call. The displayed metric
        # remains null; only explicit adapter metadata can make it measured.
        if actions and measured is None:
            raise BudgetStop("provider_usage_unavailable")
        if (measured if measured is not None else 0) >= min(caps):
            raise BudgetStop("provider_budget_reached")


def reserve(root, run_id, state, sequence, current, frozen):
    check(state, current, frozen)
    if state["actions"] and sequence <= state["actions"][-1]["sequence"]:
        raise ValueError("Budget reservation cannot be replayed")
    state["actions"].append({"sequence": sequence, "usage": None})
    save(root, run_id, state)  # A crash/failure consumes this reservation.


def observe(root, run_id, state, sequence, usage):
    if not state["actions"] or state["actions"][-1]["sequence"] != sequence:
        raise ValueError("Provider usage requires the current budget reservation")
    state["actions"][-1]["usage"] = validate_usage(usage)
    save(root, run_id, state)
