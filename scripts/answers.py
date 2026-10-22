"""Private, deterministic answer preparation. No network, model, or application writes.

Call mutations under the workspace writer lock. See docs/answers.md for the wire
schemas and the ready/preflight contract. Paths returned here are workspace-relative.
"""

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

TYPES = {"text", "number", "boolean", "single_select", "multi_select"}
SCOPE_KEYS = {"country", "company", "application_id", "consent_id"}
BASES = {"saved_answer", "authorized_default", "employer_preference"}
KINDS = {"fact", "experience_duration", "routine_preference", "legal_authorization",
         "sponsorship", "residency", "consent", "salary", "language", "qualification"}
QUESTION_KEYS = {"question", "type", "options", "units", "scope", "required", "kind",
                 "skill", "field_id", "employer_preferred_option"}
ANSWER_KEYS = {"id", "version", "value", "scope", "type", "options", "units", "kind",
               "skill", "basis", "authorized", "aliases", "confirmed_at", "source",
               "effective_from", "valid_until", "review_due_at"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _same(a, b):
    # JSON booleans must never equal numerical 0/1.
    return _json(a) == _json(b)


def normalize(text):
    """Only Unicode normalization, case folding, and whitespace collapsing."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise ValueError("Invalid identifier")
    return value


def _time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Timestamp must include a UTC offset")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return result


def _path(root, relative):
    base = Path(root).resolve()
    path = base / relative
    if not path.resolve().is_relative_to(base):
        raise ValueError("Answer data must stay inside the private workspace")
    # Do not let an in-workspace symlink redirect authoritative writes either.
    if any(p.is_symlink() for p in (path, *path.parents) if p != base and p.is_relative_to(base)):
        raise ValueError("Answer data paths must not contain symlinks")
    return path


def _read(root, relative, default=None):
    path = _path(root, relative)
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _write(root, relative, value, immutable=False):
    path = _path(root, relative)
    content = (_json(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() == content:
            return
        if immutable:
            raise ValueError("An immutable answer snapshot was modified")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".answers-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)  # mkstemp creates mode 0600.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _scope(value):
    if value == "global":
        return {}
    if not isinstance(value, dict) or set(value) - SCOPE_KEYS:
        raise ValueError("Scope must be an explicit object (or global)")
    if any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise ValueError("Scope values must be nonempty strings")
    return {k: normalize(v) for k, v in value.items()}


def _units(value):
    if value is None or isinstance(value, str) and value.strip():
        return value
    if (isinstance(value, dict) and value and not set(value) - {"unit", "currency", "period"}
            and all(isinstance(v, str) and v.strip() for v in value.values())):
        return value
    raise ValueError("Units must be null, a unit string, or unit/currency/period object")


def _options(value):
    if not isinstance(value, list):
        raise ValueError("Options must be a list")
    if any(not isinstance(x, (str, bool, int, float)) or x is None for x in value):
        raise ValueError("Options must contain scalar values")
    keys = [normalize(x) if isinstance(x, str) else _json(x) for x in value]
    if len(keys) != len(set(keys)):
        raise ValueError("Options must be unambiguous")
    _json(value)
    return value


def _question(value):
    if not isinstance(value, dict) or set(value) - QUESTION_KEYS:
        raise ValueError("Question has unsupported fields")
    if not isinstance(value.get("question"), str) or not value["question"].strip():
        raise ValueError("Exact question wording is required")
    q = copy.deepcopy(value)
    q.setdefault("type", "text")
    q.setdefault("options", [])
    q.setdefault("units", None)
    q.setdefault("scope", {})
    q.setdefault("required", True)
    q.setdefault("kind", "fact")
    if q["type"] not in TYPES or q["kind"] not in KINDS or type(q["required"]) is not bool:
        raise ValueError("Invalid question type, kind, or required flag")
    _options(q["options"])
    _units(q["units"])
    _scope(q["scope"])
    if q["type"] in {"single_select", "multi_select"} and not q["options"]:
        raise ValueError("Select questions need exact options")
    if "field_id" in q:
        _id(q["field_id"])
    if "skill" in q and (not isinstance(q["skill"], str) or not q["skill"].strip()):
        raise ValueError("Skill must be an explicit name")
    if "employer_preferred_option" in q and not any(
            _same(q["employer_preferred_option"], x) for x in q["options"]):
        raise ValueError("Employer-preferred option must occur in the exact options")
    return q


def _identity(q):
    return {"question": normalize(q["question"]), "type": q["type"],
            "options": sorted(q["options"], key=_json), "units": q["units"],
            "scope": _scope(q["scope"]), "kind": q["kind"],
            "skill": normalize(q.get("skill", ""))}


def _descriptor(entry, alias):
    if isinstance(alias, str):
        alias = {"question": alias}
    if not isinstance(alias, dict) or set(alias) - (QUESTION_KEYS - {"field_id", "required", "employer_preferred_option"}):
        raise ValueError("Invalid approved alias")
    inherited = {k: entry[k] for k in ("type", "options", "units", "scope", "kind", "skill") if k in entry}
    return _question({**inherited, **alias})


def _entry_check(entry, now, importing=False):
    if not isinstance(entry, dict):
        raise ValueError("Answer must be an object")
    if importing and (set(entry) - ANSWER_KEYS or not {"id", "value", "scope", "type", "aliases", "source", "confirmed_at"} <= set(entry)):
        raise ValueError("Imported answer schema is invalid")
    _id(entry.get("id"))
    if "value" not in entry:
        raise ValueError("Answer needs a value (null for unknown)")
    if "version" in entry and (type(entry["version"]) not in {str, int} or not str(entry["version"])):
        raise ValueError("Invalid answer version")
    scope = _scope(entry.get("scope", {}))
    _units(entry.get("units"))
    _options(entry.get("options", []))
    kind = entry.get("kind", "fact")
    basis = entry.get("basis", "saved_answer")
    if kind not in KINDS or basis not in BASES or entry.get("type", "text") not in TYPES:
        raise ValueError("Invalid answer kind, basis, or type")
    if kind == "consent" and not (scope.get("application_id") or scope.get("company") and scope.get("consent_id")):
        raise ValueError("Consent requires an application or company-and-notice scope")
    if kind in {"legal_authorization", "sponsorship", "residency"} and not scope.get("country"):
        raise ValueError("Work authorization, sponsorship, and residency require a country scope")
    if kind == "experience_duration" and entry.get("units") is None:
        raise ValueError("Experience duration requires explicit units")
    if kind == "salary" and entry.get("type") == "number" and not (
            isinstance(entry.get("units"), dict) and {"currency", "period"} <= set(entry["units"])):
        raise ValueError("Numeric salary requires currency and period")
    if basis != "saved_answer":
        if entry.get("authorized") is not True:
            raise ValueError("Defaults require explicit saved authorization")
        if basis == "authorized_default" and kind != "experience_duration":
            raise ValueError("Only documented skill-duration defaults are supported")
        if basis == "employer_preference" and kind != "routine_preference":
            raise ValueError("Employer preference requires an explicit routine preference entry")
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list):
        raise ValueError("Aliases must be a list")
    for alias in aliases:
        descriptor = _descriptor(entry, alias)
        if (descriptor["kind"] != kind or descriptor["type"] != entry.get("type", "text")
                or not _same(descriptor["units"], entry.get("units"))):
            raise ValueError("Aliases cannot change legal meaning, type, or units")
        # An alias cannot broaden a source scope.
        if any(_scope(descriptor["scope"]).get(k) != v for k, v in scope.items()):
            raise ValueError("Alias cannot broaden answer scope")
    if importing and (not isinstance(entry["source"], str) or not entry["source"].strip()):
        raise ValueError("Confirmation source is required")
    confirmed = _time(entry["confirmed_at"]) if entry.get("confirmed_at") else None
    if importing and (confirmed is None or confirmed > now):
        raise ValueError("Confirmation must not be in the future")
    for key in ("effective_from", "valid_until", "review_due_at"):
        if entry.get(key):
            instant = _time(entry[key])
            if key != "effective_from" and confirmed and instant <= confirmed:
                raise ValueError("Expiry/review must follow confirmation")
            if importing and key != "effective_from" and instant <= now:
                raise ValueError("Cannot import an expired answer")
    _json(entry)


def _load_bank(root):
    bank = _read(root, "profile/answers.json", {"schema_version": 1, "answers": []})
    if not isinstance(bank, dict) or type(bank.get("schema_version", 1)) is not int or bank.get("schema_version", 1) != 1:
        raise ValueError("Unsupported answer bank schema")
    if not isinstance(bank.get("answers"), list):
        raise ValueError("Answer bank needs an answers list")
    if any(not isinstance(a, dict) for a in bank["answers"]):
        raise ValueError("Source answers must be objects")
    ids = [_id(a.get("id")) for a in bank["answers"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source answer IDs")
    skills = bank.get("documented_skills", [])
    if not isinstance(skills, list) or any(not isinstance(s, str) or not s.strip() for s in skills):
        raise ValueError("Documented skills must be explicit names")
    return bank


def bank_version(bank):
    """Content version; transaction ledgers do not affect application facts."""
    return _hash({k: v for k, v in bank.items() if k not in {"imports", "applied_changes"}})


def _source_version(entry):
    return entry.get("version", _hash(entry))


def _application_scope(record, q):
    scope = {"application_id": record["application_id"]}
    for key in ("company", "country"):
        if record.get(key):
            scope[key] = record[key]
    for key, value in _scope(q["scope"]).items():
        if key in scope and normalize(scope[key]) != value:
            return None
        scope[key] = value
    return _scope(scope)


def _matches(entry, alias, q, record):
    if normalize(alias["question"]) != normalize(q["question"]):
        return False
    if (alias["type"] != q["type"] or alias["kind"] != q["kind"]
            or not _same(alias["units"], q["units"])
            or normalize(alias.get("skill", "")) != normalize(q.get("skill", ""))):
        return False
    if not _same(sorted(alias["options"], key=_json), sorted(q["options"], key=_json)):
        return False
    actual = _application_scope(record, q)
    if actual is None:
        return False
    return all(actual.get(k) == v for k, v in {**_scope(entry.get("scope", {})), **_scope(alias["scope"])}.items())


def _value(entry, q, bank, now):
    try:
        _entry_check(entry, now)
    except (ValueError, KeyError, TypeError):
        return None, "invalid_source"
    if not entry.get("confirmed_at") or not entry.get("source"):
        return None, "unconfirmed_source"
    if _time(entry["confirmed_at"]) > now or entry.get("effective_from") and _time(entry["effective_from"]) > now:
        return None, "not_effective"
    if any(entry.get(k) and now >= _time(entry[k]) for k in ("valid_until", "review_due_at")):
        return None, "expired_source"
    basis = entry.get("basis", "saved_answer")
    if basis == "authorized_default":
        if normalize(q.get("skill", "")) not in {normalize(s) for s in bank.get("documented_skills", [])}:
            return None, "undocumented_skill"
    value = entry["value"]
    if value is None:
        return None, "unknown_answer"
    if basis == "employer_preference":
        # The saved value is the authorized set, not a general "choose anything" flag.
        preferred = q.get("employer_preferred_option")
        if not isinstance(value, list) or not any(_same(preferred, x) for x in value):
            return None, "unauthorized_preference"
        value = preferred
    return _selected(value, q)


def _selected(value, q):
    kind = q["type"]
    if kind == "text" and isinstance(value, str) and value.strip():
        return value, None
    if kind == "boolean" and type(value) is bool:
        return value, None
    if kind == "number" and type(value) in {int, float} and math.isfinite(value):
        return value, None
    if kind in {"single_select", "multi_select"}:
        values = value if kind == "multi_select" and isinstance(value, list) else [value]
        if kind == "multi_select" and (not isinstance(value, list) or not value):
            return None, "invalid_value"
        selected = []
        for item in values:
            matches = [x for x in q["options"] if _same(item, x) or
                       isinstance(item, str) and isinstance(x, str) and normalize(item) == normalize(x)]
            if len(matches) != 1 or any(_same(matches[0], x) for x in selected):
                return None, "invalid_options"
            selected.append(matches[0])
        return (selected if kind == "multi_select" else selected[0]), None
    return None, "invalid_value"


def _resolve(bank, record, q, now):
    candidates = []
    for entry in bank["answers"]:
        try:
            if any(_matches(entry, _descriptor(entry, a), q, record) for a in entry.get("aliases", [])):
                candidates.append(entry)
        except (ValueError, KeyError, TypeError):
            # Malformed legacy sources cannot authorize an answer.
            continue
    explicit = [e for e in candidates if e.get("basis", "saved_answer") == "saved_answer"]
    if explicit:
        candidates = explicit  # Unknown/expired explicit facts never fall through to defaults.
    if len(candidates) != 1:
        return None, None, "ambiguous_alias" if candidates else "unmatched_question"
    entry = candidates[0]
    value, error = _value(entry, q, bank, now)
    return entry, value, error


def _rows(bank, record, questions, now):
    app = _id(record["application_id"])
    rows, seen = [], set()
    for question in questions:
        q = _question(question)
        occurrence = "occ-" + _hash({"application_id": app, "question": q})[:24]
        if occurrence in seen:
            raise ValueError("Repeated identical fields need distinct field_id values")
        seen.add(occurrence)
        entry, value, error = _resolve(bank, record, q, now)
        identity = _identity(q)
        # Country-sensitive unknowns must not become a cross-country inbox item.
        if record.get("country"):
            identity["scope"].setdefault("country", normalize(record["country"]))
        if q["kind"] == "consent":
            identity["scope"].setdefault("application_id", normalize(app))
        canonical = "q-" + _hash({"source_id": entry["id"]})[:24] if entry else "q-" + _hash(identity)[:24]
        row = {**q, "application_id": app, "question_id": canonical, "occurrence_id": occurrence,
               "answer": value, "resolution": error or "resolved"}
        if entry:
            row.update(source_answer_id=entry["id"], source_version=_source_version(entry),
                       source_fingerprint=_hash(entry), source_value=copy.deepcopy(entry["value"]),
                       source_scope=copy.deepcopy(entry.get("scope", {})),
                       basis=entry.get("basis", "saved_answer"), source=copy.deepcopy(entry.get("source")),
                       confirmed_at=entry.get("confirmed_at"))
        rows.append(row)
    return sorted(rows, key=lambda row: row["occurrence_id"])


def _blockers(rows):
    return sorted({r["question_id"] for r in rows if r["answer"] is None or r["resolution"] != "resolved"})


def _inbox(root):
    return _read(root, "profile/question-inbox.json", {"schema_version": 1, "questions": []})


def _occurrences(inbox, app=None):
    return [o for item in inbox["questions"] for o in item["occurrences"]
            if app is None or o["application_id"] == app]


def _questions_for(inbox, app):
    return [{k: o[k] for k in QUESTION_KEYS if k in o} for o in _occurrences(inbox, app)]


def _update_inbox(root, record, rows):
    occurrences = [o for o in _occurrences(_inbox(root)) if o["application_id"] != record["application_id"]]
    for row in rows:
        occurrences.append({**row, "company": record.get("company"),
                            "listing_url": record.get("listing_url"),
                            "destination_url": record.get("destination_url", record.get("application_url")),
                            "canonical_listing_url": record.get("canonical_listing_url"),
                            "record_path": f"applications/records/{record['application_id']}/record.json"})
    groups = {}
    for occurrence in sorted(occurrences, key=lambda o: o["occurrence_id"]):
        groups.setdefault(occurrence["question_id"], []).append(occurrence)
    items = [{"question_id": key, "status": "unresolved" if _blockers(group) else "resolved",
              "application_ids": sorted({o["application_id"] for o in group}), "occurrences": group}
             for key, group in sorted(groups.items())]
    _write(root, "profile/question-inbox.json", {"schema_version": 1, "questions": items})


def prepare_answers(root, record, questions, now):
    """Freeze exact answer rows; return blockers for the parent's state transition."""
    now = _time(now)
    if not isinstance(questions, list):
        raise ValueError("Questions must be a list")
    bank = _load_bank(root)  # Deliberately one read for the entire form.
    rows = _rows(bank, record, questions, now)
    version = _hash(rows)
    relative = f"applications/records/{_id(record['application_id'])}/answers/{version}.json"
    _write(root, relative, rows, immutable=True)
    _update_inbox(root, record, rows)
    blockers = _blockers(rows)
    return {"application_id": record["application_id"], "answers": rows, "answers_path": relative,
            "snapshot_version": version, "bank_version": bank_version(bank),
            "inbox_path": "profile/question-inbox.json", "blocked_on_answer_ids": blockers,
            "unresolved_ids": blockers, "ready": not blockers,
            "status": "awaiting_answer" if blockers else "preparing"}


def validate_snapshot(root, record, answers, now, *, allow_legacy=False):
    """Never mutate a frozen snapshot. Legacy nonnull rows require explicit opt-in."""
    now = _time(now)
    bank = _load_bank(root)
    if not isinstance(answers, list):
        raise ValueError("Snapshot must be an exact answers list")
    errors, legacy, seen = [], False, set()
    for row in answers:
        code = None
        if not isinstance(row, dict) or row.get("answer") is None:
            code = "unknown_answer"
        elif not row.get("source_fingerprint"):
            legacy = True
            # A partial v1 row is not a legacy/manual escape hatch.
            partial = any(k in row for k in ("occurrence_id", "source_version", "resolution"))
            if not allow_legacy or partial:
                code = "missing_provenance"
            elif not isinstance(row.get("question"), str) or not row["question"].strip():
                code = "invalid_question"
            elif "type" in row:
                try:
                    _, code = _selected(row["answer"], _question({k: row[k] for k in QUESTION_KEYS if k in row}))
                except (ValueError, TypeError):
                    code = "invalid_question"
        else:
            try:
                q = {k: row[k] for k in QUESTION_KEYS if k in row}
                expected = _rows(bank, record, [q], now)[0]
                if row.get("occurrence_id") in seen:
                    code = "duplicate_occurrence"
                elif expected["answer"] is None:
                    code = expected["resolution"]
                elif not _same(row, expected):
                    code = "changed_provenance_or_value"
                seen.add(row.get("occurrence_id"))
            except (ValueError, KeyError, TypeError):
                code = "invalid_snapshot"
        if code:
            errors.append({"question_id": row.get("question_id") if isinstance(row, dict) else None,
                           "occurrence_id": row.get("occurrence_id") if isinstance(row, dict) else None,
                           "code": code})
    # Prevent dropping a required/unresolved row from a freshly prepared form.
    known = _occurrences(_inbox(root), record["application_id"])
    if not legacy and known:
        for occurrence in known:
            if occurrence["occurrence_id"] not in seen:
                errors.append({"question_id": occurrence["question_id"],
                               "occurrence_id": occurrence["occurrence_id"], "code": "missing_occurrence"})
    return {"valid": not errors, "errors": errors, "legacy_manual": legacy}


def _records(root):
    directory = _path(root, "applications/records")
    return [_read(root, str(p.relative_to(Path(root).resolve())))
            for p in sorted(directory.glob("*/record.json"))]


def context(root, now):
    """Write a compact private context; events/logging/timestamps are excluded."""
    now = _time(now)
    bank = _load_bank(root)
    compact = []
    for entry in sorted(bank["answers"], key=lambda e: e["id"]):
        try:
            descriptor = _descriptor(entry, {"question": "Source validity"})
            if entry.get("basis") == "employer_preference":
                # Context summarizes the authorized set, not a selected employer choice.
                choices = entry.get("value")
                usable = isinstance(choices, list) and bool(choices) and all(
                    _value(entry, {**descriptor, "employer_preferred_option": v}, bank, now)[1] is None
                    for v in choices)
            else:
                usable = _value(entry, descriptor, bank, now)[1] is None
        except (ValueError, KeyError, TypeError):
            usable = False
        compact.append({**{k: entry.get(k) for k in ("id", "value", "scope", "basis", "valid_until", "review_due_at")},
                        "version": _source_version(entry), "usable": bool(usable)})
    registry = _read(root, "resumes/registry.json", {"releases": []})
    releases = [{k: entry.get(k) for k in ("id", "scope", "purpose", "status", "effective_from",
                 "valid_until", "review_due_at", "source_master_id", "superseded_by", "sha256")}
                for entry in registry["releases"]]
    blockers = [{"application_id": r["application_id"], "status": r.get("status"),
                 "blocked_on_answer_ids": sorted(r.get("blocked_on_answer_ids", []))}
                for r in _records(root) if r.get("status") == "awaiting_answer"]
    payload = {"schema_version": 1, "bank_version": bank_version(bank), "answers": compact,
               "preferences": _read(root, "profile/preferences.json", bank.get("preferences", {})),
               "resumes": sorted(releases, key=lambda r: (r["scope"] or "", r["id"])),
               "blockers": sorted(blockers, key=lambda r: r["application_id"])}
    payload["version"] = _hash(payload)
    _write(root, "profile/context.json", payload)
    return {**payload, "path": "profile/context.json"}


def _affected(root, before, after, now):
    inbox = _inbox(root)
    affected = []
    for record in _records(root):
        questions = _questions_for(inbox, record["application_id"])
        if not questions:
            continue
        old = _rows(before, record, questions, now)
        new = _rows(after, record, questions, now)
        if not _same(old, new):
            affected.append({"application_id": record["application_id"], "status": record.get("status"),
                             "before_blocked_on_answer_ids": _blockers(old),
                             "blocked_on_answer_ids": _blockers(new),
                             "answers_resolved": not _blockers(new),
                             "execution_checks_required": True})
    return affected


def import_answers(root, change, now, *, preview=False):
    """Validate the whole batch, preview affected jobs, then replace one bank file.

    Parent MUST hold its writer lock across this call. The import receipt and
    deduplication ledger commit in the same JSON replace as the facts.
    """
    now = _time(now)
    if (not isinstance(change, dict) or set(change) != {"schema_version", "import_id", "base_version", "changes"}
            or type(change["schema_version"]) is not int or change["schema_version"] != 1
            or not isinstance(change["changes"], list) or not change["changes"]):
        raise ValueError("Invalid answer-change schema")
    identity, fingerprint = _id(change["import_id"]), _hash(change)
    bank = _load_bank(root)
    prior = bank.get("imports", {}).get(identity)
    if prior:
        if prior["fingerprint"] != fingerprint:
            raise ValueError("Import ID was reused with different content")
        return {**prior["result"], "already_imported": True, "persisted": True,
                "imported_bank_version": prior["result"]["bank_version"], "bank_version": bank_version(bank)}
    if change["base_version"] != bank_version(bank):
        raise ValueError("Stale answer-bank base version; refresh before importing")
    proposed = copy.deepcopy(bank)
    applied = proposed.setdefault("applied_changes", {})
    source_ids, change_ids, applied_ids = set(), set(), []
    for item in change["changes"]:
        if not isinstance(item, dict) or set(item) != {"change_id", "answer"}:
            raise ValueError("Invalid change entry")
        cid = _id(item["change_id"])
        entry = copy.deepcopy(item["answer"])
        if not isinstance(entry, dict):
            raise ValueError("Answer must be an object")
        _id(entry.get("id"))
        if cid in change_ids or entry["id"] in source_ids:
            raise ValueError("Duplicate change or source ID within batch")
        change_ids.add(cid)
        source_ids.add(entry["id"])
        item_hash = _hash(item)
        if cid in applied:
            if applied[cid]["fingerprint"] != item_hash:
                raise ValueError("Change ID was reused with different content")
            continue
        _entry_check(entry, now, importing=True)
        old = next((e for e in proposed["answers"] if e["id"] == entry["id"]), None)
        if old and "version" in entry and _same(entry.get("version"), old.get("version")) and not _same(old, entry):
            raise ValueError("Changed source must use a new version (or omit for a content version)")
        if entry.get("basis") == "authorized_default" and normalize(entry.get("skill", "")) not in {
                normalize(s) for s in proposed.get("documented_skills", [])}:
            raise ValueError("Default requires a saved documented skill")
        # Validate the selected value even when aliases have not yet been added.
        for descriptor in [_question({"question": "schema", **{k: entry[k] for k in
                ("type", "options", "units", "scope", "kind", "skill") if k in entry}})] + [
                _descriptor(entry, alias) for alias in entry["aliases"]]:
            if entry["value"] is not None:
                if entry.get("basis") == "employer_preference":
                    if not isinstance(entry["value"], list) or not entry["value"] or any(
                            _selected(v, descriptor)[1] for v in entry["value"]):
                        raise ValueError("Invalid authorized preference options")
                elif _selected(entry["value"], descriptor)[1]:
                    raise ValueError("Answer value does not match type/options")
        proposed["answers"] = [e for e in proposed["answers"] if e["id"] != entry["id"]] + [entry]
        applied[cid] = {"fingerprint": item_hash, "answer_id": entry["id"], "import_id": identity}
        applied_ids.append(cid)
    proposed["answers"].sort(key=lambda e: e["id"])
    result = {"schema_version": 1, "import_id": identity, "already_imported": False,
              "persisted": not preview, "bank_version": bank_version(proposed),
              "applied_change_ids": applied_ids, "affected_jobs": _affected(root, bank, proposed, now)}
    if not preview:
        proposed.setdefault("imports", {})[identity] = {"fingerprint": fingerprint,
            "confirmed_at": now.isoformat(), "result": result}
        _write(root, "profile/answers.json", proposed)
    return result


def requeue(root, now, *, resume_check=None, eligibility_check=None, record_snapshot=None):
    """Propose runnable jobs only after ALL blockers and execution gates pass.

    Checks take (root, record, now) and must return True. No callbacks means no
    execution authorization. The parent persists updates and repeats checks at
    execution time; neither historical attempts nor records are changed here.
    """
    now = _time(now)
    bank, inbox = _load_bank(root), _inbox(root)
    updates, waiting, untouched = [], [], []
    for record in (_records(root) if record_snapshot is None else record_snapshot):
        app = record["application_id"]
        if record.get("status") != "awaiting_answer" or record.get("submissions"):
            untouched.append(app)
            continue
        questions = _questions_for(inbox, app)
        rows = _rows(bank, record, questions, now)
        # A blocker not represented by the form cannot silently disappear.
        known = {o["question_id"] for o in _occurrences(inbox, app)}
        missing = set(record.get("blocked_on_answer_ids", [])) - known
        blockers = sorted(set(_blockers(rows)) | missing)
        reasons = []
        if blockers:
            reasons.append("unresolved_answers")
        elif not questions:
            reasons.append("missing_form_questions")
        else:
            for name, callback in (("resume", resume_check), ("eligibility", eligibility_check)):
                if callback is None:
                    reasons.append(name + "_check_required")
                else:
                    try:
                        passed = callback(root, copy.deepcopy(record), now) is True
                    except (ValueError, OSError):
                        passed = False
                    if not passed:
                        reasons.append(name + "_check_failed")
        update = {"application_id": app, "blocked_on_answer_ids": blockers}
        if reasons:
            waiting.append({**update, "status": "awaiting_answer", "reasons": reasons})
        else:
            updates.append({**update, "status": "preparing", "execution_checks_required": True})
    return {"updates": updates, "runnable_ids": [u["application_id"] for u in updates],
            "waiting": waiting, "untouched_ids": untouched}
