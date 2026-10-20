"""Deterministically compare a visible application snapshot with frozen inputs.

This module is deliberately read-only. It does not inspect a browser, open files,
or call a model. The adapter supplies the accessibility-derived snapshot, including
the digest of the actual resume attachment it observes.
"""

import hashlib
import json
import re


SCHEMA_VERSION = 1
_MISSING = object()


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _answers_bytes(value):
    """Match workspace.atomic_json's persisted encoding for the frozen answers."""
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _field_key(field):
    # occurrence_id is the frozen answer core's stable identity. field_id is
    # accepted for adapters which preserve the exact source field identifier.
    return field.get("occurrence_id", field.get("field_id", field.get("id")))


def _same(left, right):
    # JSON encoding preserves distinctions such as true versus 1.
    return _json(left) == _json(right)


def _prepared_identity(prepared):
    identity = prepared.get("attempt_id")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("prepared.attempt_id is required")
    return identity


def _validate_input(prepared, answers, visible):
    if not isinstance(prepared, dict):
        raise ValueError("prepared must be an object")
    _prepared_identity(prepared)
    if not isinstance(answers, list):
        raise ValueError("answers must be the frozen answer list")
    if not isinstance(visible, dict) or not isinstance(visible.get("fields"), list):
        raise ValueError("visible.fields must be a list")
    for key in ("answers_sha256",):
        if not isinstance(prepared.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", prepared[key]):
            raise ValueError(f"prepared.{key} must be a SHA-256 hex digest")
    expected_resume = prepared.get("sha256", prepared.get("resume_sha256"))
    if not isinstance(expected_resume, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_resume):
        raise ValueError("prepared.sha256 must be the frozen resume SHA-256")
    return expected_resume


def _difference(code, field_id=None, question=None, expected=_MISSING, actual=_MISSING):
    result = {"code": code}
    if field_id is not None:
        result["field_id"] = field_id
    if question is not None:
        result["question"] = question
    if expected is not _MISSING:
        result["expected"] = expected
    if actual is not _MISSING:
        result["actual"] = actual
    return result


def review(prepared, answers, visible):
    """Return a verdict for one attempt; all comparisons are exact and local.

    `prepared` requires attempt_id, answers_sha256 and sha256 (the resume bytes).
    `answers` is the exact list saved in the frozen answers file. `visible` has
    attempt_id, resume_sha256 and fields containing id/occurrence_id, question,
    type and value. Field values are included in differences, so verdicts are
    private application data and must be stored only with the private attempt.
    """
    expected_resume = _validate_input(prepared, answers, visible)
    attempt_id = _prepared_identity(prepared)
    answers_hash = _sha(_answers_bytes(answers))
    differences = []

    if answers_hash != prepared["answers_sha256"]:
        differences.append(_difference("frozen_answers_hash_mismatch",
                                       expected=prepared["answers_sha256"], actual=answers_hash))
    if visible.get("attempt_id") != attempt_id:
        differences.append(_difference("attempt_mismatch", expected=attempt_id,
                                       actual=visible.get("attempt_id")))
    visible_resume = visible.get("resume_sha256")
    if visible_resume != expected_resume:
        differences.append(_difference("resume_hash_mismatch", expected=expected_resume,
                                       actual=visible_resume))

    expected_by_id = {}
    for row in answers:
        if not isinstance(row, dict):
            differences.append(_difference("invalid_frozen_field"))
            continue
        identity = _field_key(row)
        if not isinstance(identity, str) or not identity:
            differences.append(_difference("frozen_field_missing_id", question=row.get("question")))
        elif identity in expected_by_id:
            differences.append(_difference("duplicate_frozen_field", identity,
                                           row.get("question")))
        else:
            expected_by_id[identity] = row

    seen = set()
    for field in visible["fields"]:
        if not isinstance(field, dict):
            differences.append(_difference("invalid_visible_field"))
            continue
        identity = _field_key(field)
        if not isinstance(identity, str) or not identity:
            differences.append(_difference("visible_field_missing_id", question=field.get("question")))
            continue
        if identity in seen:
            differences.append(_difference("duplicate_visible_field", identity,
                                           field.get("question")))
            continue
        seen.add(identity)
        expected = expected_by_id.get(identity)
        if expected is None:
            differences.append(_difference("unknown_visible_field", identity,
                                           field.get("question")))
            continue
        for attribute, expected_value, actual_value in (
                ("question", expected.get("question", _MISSING), field.get("question", _MISSING)),
                ("type", expected.get("type", _MISSING), field.get("type", _MISSING))):
            if expected_value is _MISSING or actual_value is _MISSING:
                differences.append(_difference("field_" + attribute + "_missing", identity,
                                               expected.get("question"),
                                               expected_value if expected_value is not _MISSING else _MISSING,
                                               actual_value if actual_value is not _MISSING else _MISSING))
                continue
            if not _same(expected_value, actual_value):
                differences.append(_difference("field_" + attribute + "_mismatch", identity,
                                               expected.get("question"), expected_value, actual_value))
        if "answer" not in expected or "value" not in field:
            differences.append(_difference("field_value_missing", identity,
                                           expected.get("question")))
        elif not _same(expected["answer"], field["value"]):
            differences.append(_difference("field_value_mismatch", identity,
                                           expected.get("question"), expected["answer"],
                                           field["value"]))

    for identity, expected in expected_by_id.items():
        if identity not in seen:
            differences.append(_difference("missing_visible_field", identity,
                                           expected.get("question")))

    # Stable ordering makes stored verdicts reproducible.
    differences.sort(key=lambda item: (item.get("field_id", ""), item["code"],
                                       item.get("question", ""), _json(item)))
    passed = not differences
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": passed,
        "stop_reason": None if passed else "review_mismatch",
        "differences": differences,
        "binding": {
            "answers_sha256": answers_hash,
            "resume_sha256": expected_resume,
            "attempt_id": attempt_id,
        },
    }


def validate_verdict(verdict, prepared, answers, visible):
    """Validate a stored verdict against the exact current inputs and snapshot.

    A failed review can be valid evidence of a blocker. Callers must require both
    `valid` and `passed` before allowing the surrounding preflight to continue.
    """
    try:
        current = review(prepared, answers, visible)
    except (TypeError, ValueError, OverflowError) as exc:
        return {"valid": False, "passed": False, "errors": [str(exc)]}
    try:
        matches = isinstance(verdict, dict) and _same(verdict, current)
    except (TypeError, ValueError, OverflowError):
        matches = False
    if not matches:
        return {"valid": False, "passed": False,
                "errors": ["stored verdict does not match the current attempt snapshot"]}
    return {"valid": True, "passed": current["passed"], "errors": []}
