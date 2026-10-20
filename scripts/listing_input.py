"""Validate extractor or legacy capture input before any workspace writes.

No workspace/profile reads. ``validate_listing`` returns a new normalized dict;
``ListingError`` exposes ``field`` and ``exit_code`` for the capture CLI.
"""
import copy
import json
import re
import sys
from urllib.parse import parse_qs, urlsplit


class ListingError(ValueError):
    exit_code = 2

    def __init__(self, field, reason="missing required field"):
        self.field = field
        super().__init__(f"{field}: {reason}")


TEXT_FIELDS = {
    "company", "role", "title", "location", "job_description", "platform",
    "platform_job_id", "employer_requisition_id", "source", "workplace_type",
    "employment_type", "posted_age", "applied_by", "notes", "country",
}
URL_FIELDS = {
    "listing_url", "application_url", "canonical_listing_url", "canonical_url",
    "source_url", "destination_url", "apply_url",
}
ALLOWED_FIELDS = TEXT_FIELDS | URL_FIELDS | {"easy_apply", "hiring_team"}


def _url(value, field):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not re.search(r"[\s\x00-\x1f\x7f\\]", value))
        parsed.port  # Validate malformed/out-of-range ports, too.
    except ValueError:
        valid = False
    if not valid:
        raise ListingError(field, "expected an absolute HTTP(S) URL without credentials")
    return parsed


def _linkedin_id(value, field, required=True):
    parsed = _url(value, field)
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)*linkedin\.com", parsed.hostname, re.I):
        raise ListingError(field, "expected a LinkedIn URL")
    match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d+)/?", parsed.path)
    query = parse_qs(parsed.query, keep_blank_values=True).get("currentJobId", [])
    if len(query) > 1 or (query and not re.fullmatch(r"\d+", query[0])):
        raise ListingError(field, "ambiguous or invalid URL job ID")
    if match and query and match.group(1) != query[0]:
        raise ListingError(field, "URL job IDs disagree")
    identity = match.group(1) if match else (query[0] if len(query) == 1 else None)
    if required and not identity:
        raise ListingError(field, "missing LinkedIn job identity")
    return identity


def validate_listing(data):
    """Validate the complete input, then map title/source/apply aliases.

    Extractor payloads have source='linkedin_job_page'. Legacy capture payloads
    use role/listing_url/application_url and remain supported. Unknown keys are
    rejected so input cannot inject record status, paths, or capture timestamps.
    Description bytes (including whitespace) and optional facts are preserved.
    """
    if not isinstance(data, dict):
        raise ListingError("input", "expected a JSON object")
    unknown = set(data) - ALLOWED_FIELDS
    if unknown:
        raise ListingError(str(sorted(unknown, key=str)[0]), "unknown input field")
    result = copy.deepcopy(data)
    for field in sorted(TEXT_FIELDS | URL_FIELDS):
        value = result.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ListingError(field, "expected a non-empty string or null")
        if value is not None and field in URL_FIELDS:
            _url(value, field)
    if "easy_apply" in result and type(result["easy_apply"]) is not bool:
        raise ListingError("easy_apply", "expected a boolean")
    if result.get("hiring_team") is not None:
        poster = result["hiring_team"]
        if not isinstance(poster, dict):
            raise ListingError("hiring_team", "expected an object or null")
        for key, value in poster.items():
            field = f"hiring_team.{key}"
            if key not in {"name", "headline", "profile_url"}:
                raise ListingError(field, "unknown input field")
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ListingError(field, "expected a non-empty string or null")
            if value is not None and key == "profile_url":
                _url(value, field)

    for alias, target in (("title", "role"), ("apply_url", "application_url"),
                          ("canonical_url", "canonical_listing_url")):
        if alias in result:
            if target in result and result[target] != result[alias]:
                raise ListingError(target, f"conflicts with {alias}")
            result[target] = result.pop(alias)
    if "source_url" in result:
        if "listing_url" not in result:
            result["listing_url"] = result["source_url"]
        elif result["listing_url"] != result["source_url"]:
            # Revalidation of normalized extractor output has a canonical listing
            # URL and a separately preserved source page (possibly a collection).
            if not (result.get("source") == "linkedin_job_page"
                    and result["listing_url"] == result.get("canonical_listing_url")):
                raise ListingError("listing_url", "conflicts with source_url")
    for field in ("company", "role", "location", "listing_url", "job_description"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ListingError(field)

    if result.get("source") == "linkedin_job_page":
        if result.get("platform") != "linkedin":
            raise ListingError("platform", "expected linkedin")
        identity = result.get("platform_job_id")
        if not identity or not re.fullmatch(r"\d+", identity):
            raise ListingError("platform_job_id", "expected a numeric job ID string")
        if "easy_apply" not in result:
            raise ListingError("easy_apply")
        for field in ("listing_url", "canonical_listing_url", "source_url"):
            if field == "source_url" and field not in result:
                continue
            if not result.get(field):
                raise ListingError(field)
            observed = _linkedin_id(result[field], field, required=field == "canonical_listing_url")
            if observed is not None and observed != identity:
                raise ListingError(field, "does not match platform_job_id")
        canonical = f"https://www.linkedin.com/jobs/view/{identity}"
        result["canonical_listing_url"] = canonical
        application = result.get("application_url")
        expected_destination = canonical if result["easy_apply"] else application
        if "destination_url" not in result:
            raise ListingError("destination_url")
        if result["destination_url"] != expected_destination:
            raise ListingError("destination_url", "does not match visible apply destination")
        if result["easy_apply"] and application is not None:
            raise ListingError("application_url", "Easy Apply has no external apply URL")
        # Capture indexes listing_url. A collection page must never become the
        # job's canonical key, even when it is the observed extraction source.
        result.setdefault("source_url", result["listing_url"])
        result["listing_url"] = canonical
    return result


def main():
    """Optional stdin validator; emits no stdout on invalid input and writes no files."""
    try:
        data = json.load(sys.stdin)
        result = validate_listing(data)
    except json.JSONDecodeError:
        error = ListingError("input", "invalid JSON")
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return error.exit_code
    except ListingError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return exc.exit_code
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
