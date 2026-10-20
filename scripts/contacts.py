"""Pure, offline normalization of contacts supplied as visible listing facts.

These helpers never read a workspace, visit a profile, or enrich an identity.
The caller is responsible for supplying only facts visible in the named source.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit


CONTACT_SOURCES = frozenset({"linkedin_hiring_team", "jd_text", "confirmation_email"})
RELATIONSHIPS = frozenset({"job_poster", "hiring_team", "unknown"})
VISIBLE_FIELDS = ("name", "headline", "profile_url", "email")
CONTACT_FIELDS = frozenset(VISIBLE_FIELDS) | {
    "contact_id", "source", "captured_at", "relationship", "verified",
}
_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z"
)
_LOCAL = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_EMAIL = re.compile(_LOCAL + "@" + _LABEL + r"(?:\." + _LABEL + r")+")
_PRINTED_EMAIL = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-])" + _EMAIL.pattern
    + r"(?![A-Za-z0-9_@-]|\.[A-Za-z0-9])"
)


class ContactError(ValueError):
    """Validation failure suitable for wrapping in listing_input.ListingError."""

    exit_code = 2

    def __init__(self, field, reason):
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _timestamp(value, field, allow_datetime=False):
    if allow_datetime and isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise ContactError(field, "expected an ISO-8601 timestamp with a UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContactError(field, "invalid timestamp") from exc
    if parsed.utcoffset() is None:
        raise ContactError(field, "timestamp must include a UTC offset")
    return value


def _profile_url(value, field):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not re.search(r"[\s\x00-\x1f\x7f\\]", value))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ContactError(field, "expected an absolute HTTP(S) URL without credentials")


def _contact_id(contact):
    identity = {key: contact[key] for key in (*VISIBLE_FIELDS, "source")}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "contact-" + hashlib.sha256(encoded).hexdigest()


def validate_contacts(contacts, now, jd_text=None, email_text=None):
    """Return normalized contacts, rejecting unsupported sources and verification.

    ``now`` is an offset-aware datetime or ISO timestamp used only when a contact
    lacks ``captured_at``. Provided timestamps and visible strings stay exact.
    Email must be printed verbatim in the supplied source text; it is never
    extracted, guessed, case-folded, or associated with a person by this helper.
    Missing/None contacts normalize to []. Exact identities deduplicate in order,
    retaining the first capture. Any invalid entry rejects the entire input.
    """
    return _validate_contacts(contacts, now, jd_text, email_text)


def validate_contact_inputs(contacts, jd_text=None, email_text=None):
    """Validate listing input without inventing a capture timestamp.

    Missing capture times remain null until ``validate_contacts`` receives the
    capture operation's actual ``now``. All other checks, including explicitly
    supplied timestamps, IDs, email evidence and duplicate entries, still apply.
    These are input objects, not contacts ready for record persistence.
    """
    return _validate_contacts(contacts, None, jd_text, email_text,
                              defer_capture_time=True)


def _validate_contacts(contacts, now, jd_text, email_text, *, defer_capture_time=False):
    for field, text in (("jd_text", jd_text), ("email_text", email_text)):
        if text is not None and not isinstance(text, str):
            raise ContactError(field, "expected text or null")
    if contacts is None:
        return []
    if not isinstance(contacts, list):
        raise ContactError("contacts", "expected a list or null")
    result = []
    seen = set()
    for index, contact in enumerate(contacts):
        prefix = f"contacts[{index}]"
        if not isinstance(contact, dict):
            raise ContactError(prefix, "expected an object")
        unknown = set(contact) - CONTACT_FIELDS
        if unknown:
            raise ContactError(f"{prefix}.{sorted(unknown, key=str)[0]}", "unknown field")
        source = contact.get("source")
        if not isinstance(source, str) or source not in CONTACT_SOURCES:
            raise ContactError(f"{prefix}.source", "unsupported contact source")
        relationship = contact.get("relationship", "unknown")
        if not isinstance(relationship, str) or relationship not in RELATIONSHIPS:
            raise ContactError(f"{prefix}.relationship", "unsupported relationship")
        if contact.get("verified", False) is not False:
            raise ContactError(f"{prefix}.verified", "must be false; verification is not supported")
        normalized = {key: contact.get(key) for key in VISIBLE_FIELDS}
        for key, value in normalized.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ContactError(f"{prefix}.{key}", "expected a non-empty string or null")
        if not any(value is not None for value in normalized.values()):
            raise ContactError(prefix, "at least one visible contact fact is required")
        if normalized["profile_url"] is not None:
            _profile_url(normalized["profile_url"], f"{prefix}.profile_url")
        email = normalized["email"]
        if email is not None:
            if not _EMAIL.fullmatch(email):
                raise ContactError(f"{prefix}.email", "expected a printed email address")
            texts = {"jd_text": [jd_text], "confirmation_email": [email_text],
                     "linkedin_hiring_team": [jd_text, email_text]}[source]
            if not any(email == match.group() for text in texts if text is not None
                       for match in _PRINTED_EMAIL.finditer(text)):
                raise ContactError(f"{prefix}.email", "not printed in the supplied source text")
        captured_at = contact.get("captured_at")
        if captured_at is None:
            if not defer_capture_time:
                captured_at = _timestamp(now, f"{prefix}.captured_at", allow_datetime=True)
        else:
            captured_at = _timestamp(captured_at, f"{prefix}.captured_at")
        normalized.update(source=source, captured_at=captured_at,
                          relationship=relationship, verified=False)
        identity = _contact_id(normalized)
        if "contact_id" in contact and contact["contact_id"] != identity:
            raise ContactError(f"{prefix}.contact_id", "does not match visible identity and source")
        normalized["contact_id"] = identity
        if identity not in seen:
            result.append(normalized)
            seen.add(identity)
    return result


def contacts_json(contacts):
    """Stable JSON for one CSV cell; preserve historical facts without enrichment."""
    if contacts is None:
        contacts = []
    if not isinstance(contacts, list):
        raise ContactError("contacts", "expected a list or null")
    return json.dumps(contacts, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def from_hiring_team(card, now):
    """Convert the extractor's single visible card to zero or one contacts.

    Absent or all-null cards yield []. The structural hiring-team source supports
    relationship='hiring_team'; no job-poster relationship is inferred from a
    headline. An explicitly supplied relationship must be from the allowed enum.
    """
    if card is None:
        return []
    if not isinstance(card, dict):
        raise ContactError("hiring_team", "expected an object or null")
    allowed = {"name", "headline", "profile_url", "relationship"}
    unknown = set(card) - allowed
    if unknown:
        raise ContactError(f"hiring_team.{sorted(unknown, key=str)[0]}", "unknown field")
    contact = {key: card.get(key) for key in ("name", "headline", "profile_url")}
    contact.update(source="linkedin_hiring_team",
                   relationship=card.get("relationship", "hiring_team"))
    # Validate even an empty card's metadata before discarding it.
    if not any(contact[key] is not None for key in ("name", "headline", "profile_url")):
        relationship = contact["relationship"]
        if not isinstance(relationship, str) or relationship not in RELATIONSHIPS:
            raise ContactError("hiring_team.relationship", "unsupported relationship")
        return []
    return validate_contacts([contact], now)


def _record_contacts(record):
    if not isinstance(record, dict):
        raise ContactError("record", "expected an object")
    contacts = record.get("contacts", [])
    if not isinstance(contacts, list) or any(not isinstance(item, dict) for item in contacts):
        raise ContactError("contacts", "expected a list of objects")
    return contacts


def add_contact(record, contact, now, jd_text=None, email_text=None):
    """Return a deep copy with a validated contact appended, idempotently.

    Existing contacts and unrelated record fields remain unchanged. An existing
    identity retains its original time and verification; this is not an edit or
    verification API. Existing email contacts do not need their evidence reloaded.
    """
    existing = _record_contacts(record)
    normalized = validate_contacts([contact], now, jd_text, email_text)[0]
    result = deepcopy(record)
    if not any(item.get("contact_id") == normalized["contact_id"] for item in existing):
        result.setdefault("contacts", []).append(normalized)
    return result


def remove_contact(record, contact_id):
    """Return a deep copy with the exact contact ID removed; missing IDs are no-ops."""
    _record_contacts(record)
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ContactError("contact_id", "expected a non-empty contact ID")
    result = deepcopy(record)
    if "contacts" in result:
        result["contacts"] = [item for item in result["contacts"]
                              if item.get("contact_id") != contact_id]
    return result
