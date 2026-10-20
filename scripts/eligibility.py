"""Pure, conservative listing eligibility. No workspace, clock, model or I/O.

See docs/eligibility.md for the policy, trusted-fact and decision contracts.
"""

import math
import re
import unicodedata

try:  # Support both the CLI's scripts path and package imports.
    from . import settings
except ImportError:
    import settings


class EligibilityError(ValueError):
    """Malformed evaluator input; never a successful eligibility decision."""


_RULES = {
    "allowed_locations": "location",
    "excluded_locations": "location",
    "allowed_employment_types": "employment_type",
    "excluded_employment_types": "employment_type",
}
_YEAR = re.compile(
    r"\b(?P<lower>\d+(?:\.\d+)?)"
    r"(?:(?:\s*[-–—]\s*|\s+to\s+)(?P<upper>\d+(?:\.\d+)?))?"
    r"\s*(?P<plus>\+)?\s*(?:years?|yrs?)\b", re.I)
_EXPERIENCE = re.compile(r"\b(?:experience|exp)\b|\b(?:years?|yrs?)\s+(?:in|with)\b", re.I)
_HARD = re.compile(r"\b(?:minimum|min\.?|at least|must|requires?|required|mandatory|essential)\b")
_PREFERRED = re.compile(
    r"\b(?:preferred|preferably|desirable|ideally|optional|beneficial|bonus|advantage\w*|a plus|nice[- ]to[- ]have)\b")
_NOT_REQUIRED = re.compile(
    r"\b(?:not|never)\s+(?:strictly\s+)?(?:required|mandatory|essential|necessary)\b|"
    r"\b(?:do not|does not|don't|doesn't)\s+require\b|"
    r"\bno\s+(?:(?:prior|previous|professional|relevant)\s+)?experience\s+(?:is\s+)?required\b")
_UNCERTAIN = re.compile(
    r"\b(?:or|unless|equivalent|depending|around|approximately|about|typically|"
    r"up to|at most|maximum|max|less than|under|over|more than|waiv\w*|may|might|if|subject to|not|without)\b")


def _normal(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("’", "'").split())


def _clauses(text):
    """Keep heading scope across bullets; punctuation resets sentence scope."""
    context = None
    for line in re.split(r"[\r\n]+", text):
        line = _normal(line).strip(" \t-*•")
        line = re.sub(r"\bmin\.(?=\s*\d)", "minimum", line)
        if not line:
            continue
        heading = line.rstrip(":")
        if heading in {"requirements", "minimum requirements", "required qualifications", "qualifications"}:
            context = "hard"
            continue
        if heading in {"preferred", "preferred qualifications", "nice to have", "desirable"}:
            context = "preferred"
            continue
        if line.endswith(":") or line in {"benefits", "about us", "responsibilities", "about the company"}:
            context = None
        for clause in re.split(r"(?<!\d)[.!?;]+\s*|(?<=\d)[.!?;]+(?=\s|$)", line):
            if clause.strip():
                yield clause.strip(), context


def _experience(clause, context):
    """Return (hard lower bounds, ambiguous). Preferences never set a bound."""
    if not _EXPERIENCE.search(clause):
        # Unattributed year requirements are not automatically professional years.
        years = list(_YEAR.finditer(clause))
        duration = re.search(r"\b(?:degree|education|course|contract|founded|ago|warranty)\b", clause)
        intrinsic = any(match["plus"] or match["upper"] for match in years)
        ambiguous = (bool(years) and not duration and not _PREFERRED.search(clause) and
                     not _NOT_REQUIRED.search(clause) and (bool(_HARD.search(clause)) or intrinsic))
        return [], bool(ambiguous)
    # Canonicalize unambiguous numeric ranges before searching individual years.
    number = r"(\d+(?:\.\d+)?)"
    clause = re.sub(r"\bbetween\s+" + number + r"\s+and\s+" + number +
                    r"\s*(years?|yrs?)\b", r"\1-\2 \3", clause)
    clause = re.sub(r"\b" + number + r"\s*(?:years?|yrs?)\s*(?:[-–—]|to)\s*" +
                    number + r"\s*(years?|yrs?)\b", r"\1-\2 \3", clause)
    if re.search(r"\d\s*[,/⁄]\s*\d", clause):
        return [], True
    matches = list(_YEAR.finditer(clause))
    if not matches:
        # Word numbers/unsupported notation are evidence for review, not guesses.
        return [], bool(re.search(r"\b(?:years?|yrs?)\b", clause))
    if _NOT_REQUIRED.search(clause):
        # Compound requirements can have narrower negation scope.
        return [], len(matches) > 1 or bool(re.search(r"\b(?:but|however)\b", clause))
    if len(matches) > 1:
        return [], True
    # 'Not less than' is an explicit inclusive minimum; other negations are not.
    cleaned = re.sub(r"\b(?:not|no) less than\b", "at least", clause)
    cleaned = re.sub(r"\bnot (?:merely |just )?preferred\b", "", cleaned)
    if _UNCERTAIN.search(cleaned):
        return [], True
    preference = bool(_PREFERRED.search(cleaned))
    hard = bool(_HARD.search(cleaned))
    if preference:
        return [], hard  # Mixed must/preferred wording requires interpretation.
    match = matches[0]
    lower = float(match["lower"])
    upper = float(match["upper"]) if match["upper"] else None
    if not math.isfinite(lower) or (upper is not None and (not math.isfinite(upper) or upper < lower)):
        return [], True
    if context == "preferred" and not hard:
        return [], False
    if hard or context == "hard" or match["plus"] or upper is not None:
        # Do not turn a company's age/collective experience into an applicant minimum.
        if re.search(r"\b(?:we|our team|our company|company has)\b", cleaned) and not re.search(
                r"\b(?:you|candidate|applicant|must|required|require|minimum)\b", cleaned):
            return [], True
        return [lower], False
    return [], True


def _sponsorship(clause):
    """Return unavailable/available/ambiguous/None, with local negation scope."""
    if not re.search(r"\bsponsor(?:ship|ing|ed|s)?\b", clause):
        return None
    if re.search(r"\b(?:event|sports?|charity|conference|marketing)\b", clause) and not re.search(
            r"\b(?:visa|immigration|work permit|employment)\b", clause):
        return None
    if re.search(r"\b(?:may|might|unless|depending|guarantee\w*|not unable|not impossible|"
                 r"not unavailable|not unsupported|but|however|except|some|case.by.case|subject to)\b|"
                 r"\bfor other roles\b", clause):
        return "ambiguous"
    if re.search(r"\b(?:not required|not necessary|not needed|do not require|does not require)\b|"
                 r"\bno\s+(?:(?:visa|work permit|immigration|employment)\s+)?sponsorship\s+"
                 r"(?:is\s+)?(?:required|necessary|needed)\b", clause):
        return "ambiguous"  # Says nothing reliable about the employer's ability.
    negative = bool(re.search(
        r"\b(?:cannot|can't|can not|unable to|not able to|do not|does not|don't|doesn't|will not|won't)\b.{0,45}\bsponsor|"
        r"\bno\s+(?:(?:visa|work permit|immigration|employment)\s+)?sponsorship\b|"
        r"\bsponsorship\b.{0,35}\b(?:unavailable|unsupported|not possible|"
        r"(?:cannot|can't|will not|won't|not)\s+(?:be\s+)?(?:available|offered|provided|supported|arranged))\b|"
        r"\b(?:require|need)\b.{0,30}\bsponsorship\b.{0,30}\b(?:ineligible|not eligible)\b", clause))
    positive_clause = re.sub(
        r"\bno\s+(?:(?:visa|work permit|immigration|employment)\s+)?sponsorship\b", "", clause)
    positive_clause = re.sub(r"\bnot able to\b", "", positive_clause)
    positive = bool(re.search(
        r"\b(?:can|will|able to)\s+(?:provide\s+)?sponsor|"
        r"\bsponsorship\s+(?:is\s+)?(?:available|provided|offered|supported)\b", positive_clause))
    if negative and positive:
        return "ambiguous"
    return "unavailable" if negative else "available" if positive else "ambiguous"


def _validated_rules(policy):
    rules = policy.get("role_rules", {})
    if not isinstance(rules, dict) or set(rules) - _RULES.keys():
        raise EligibilityError("role_rules: use only the documented eligibility keys")
    for key, value in rules.items():
        if value is not None and (not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value)):
            raise EligibilityError(f"role_rules.{key}: expected non-empty strings in an array, or null")
    return rules


def evaluate(listing, policy, facts=None):
    """Return a JSON decision; facts are caller-validated current confirmations.

    A fact is usable only as {"value": bool, "current": True, "confirmed": True}.
    The caller checks source, scope and expiry before setting these flags.
    No values are inferred from unrelated profile fields. Inputs are never mutated.
    """
    if not isinstance(listing, dict) or not isinstance(policy, dict):
        raise EligibilityError("listing and policy must be objects")
    if facts is not None and not isinstance(facts, dict):
        raise EligibilityError("facts must be an object or null")
    for field in ("job_description", "location", "employment_type"):
        value = listing.get(field)
        if value is not None and not isinstance(value, str):
            raise EligibilityError(f"listing.{field}: expected text or null")
    rules = _validated_rules(policy)
    # Reuse settings validation and its established gate precedence/reason codes.
    header_reason = settings.eligibility(listing, policy)
    excluded, review, used = [], [], {}
    if header_reason:
        excluded.append(header_reason)

    def fact(key):
        record = (facts or {}).get(key)
        if (isinstance(record, dict) and record.get("confirmed") is True and
                record.get("current") is True and type(record.get("value")) is bool):
            used[key] = {"value": record["value"], "current": True, "confirmed": True}
            return record["value"]
        return None

    def requirement(key, code):
        value = fact(key)
        if value is None:
            review.append(f"{code}_unknown")
        elif value is False:
            excluded.append(f"{code}_unmet")

    for field in ("location", "employment_type"):
        allowed = rules.get("allowed_locations" if field == "location" else "allowed_employment_types")
        denied = rules.get("excluded_locations" if field == "location" else "excluded_employment_types")
        if not allowed and not denied:
            continue
        value = _normal(listing.get(field) or "")
        if not value:
            review.append(f"{field}_unknown")
        elif denied and value in {_normal(item) for item in denied}:
            excluded.append(f"{field}_excluded")
        elif allowed and value not in {_normal(item) for item in allowed}:
            # Multiple alternatives are ambiguous, never geocoded or substring-matched.
            if re.search(r"[/;|]|\bor\b", value):
                review.append(f"{field}_ambiguous")
            else:
                excluded.append(f"{field}_not_allowed")

    text = listing.get("job_description") or ""
    if not text.strip():
        review.append("description_missing")
    cap = policy.get("max_required_years")
    sponsorship = set()
    for clause, context in _clauses(text):
        if cap is not None:
            bounds, ambiguous = _experience(clause, context)
            if ambiguous:
                review.append("experience_ambiguous")
            if any(lower > cap for lower in bounds):
                excluded.append("skipped:required_years")
        sponsor = _sponsorship(clause)
        if sponsor:
            sponsorship.add(sponsor)

        # Only narrow, explicit legal/credit requirements can consume boolean facts.
        # Other legal categories and language levels always need scoped interpretation.
        explicit_hard = bool(_HARD.search(clause) or re.search(r"\bonly\b", clause))
        soft = bool(_PREFERRED.search(clause)) or (context == "preferred" and not explicit_hard)
        negated = bool(_NOT_REQUIRED.search(clause))
        hard = explicit_hard or context == "hard"
        mixed = (bool(_HARD.search(_NOT_REQUIRED.sub("", clause))) and (soft or negated) and
                 bool(re.search(r"\b(?:but|and|however|while)\b|,", clause)))
        if re.search(r"\b(?:credit[- ]bearing|academic credit)\b", clause):
            if mixed:
                review.append("credit_requirement_ambiguous")
            elif hard and not soft and not negated and not re.search(r"\b(?:not|or|unless|may)\b", clause):
                requirement("credit_bearing", "credit_bearing_requirement")
            elif not soft and not negated:
                review.append("credit_requirement_ambiguous")
        if re.search(r"\b(?:legally authori[sz]ed to work|legal right to work|right to work)\b", clause):
            if mixed:
                review.append("legal_requirement_ambiguous")
            elif hard and not soft and not negated and not re.search(r"\b(?:not|or|unless|may)\b", clause):
                requirement("legally_authorized_to_work", "work_authorization_requirement")
            elif not soft and not negated:
                review.append("legal_requirement_ambiguous")
        if re.search(r"\b(?:citizens?|citizenship|permanent residen\w*|work (?:visa|permit|authori[sz]ation)|"
                     r"nationals?|locals|local (?:candidates|applicants)|prs?|security clearance|eligible to work|authori[sz]ed to work)\b", clause):
            if (mixed or (hard and not negated and not soft)) and sponsor is None and not re.search(
                    r"\blegally authori[sz]ed to work\b", clause):
                review.append("legal_requirement_ambiguous")
        if re.search(r"\b(?:school|university|college)\b.{0,25}\b(?:approval|endorsement)\b", clause):
            if hard and not soft and not negated:
                review.append("credit_requirement_ambiguous")
        language = re.search(
            r"\b(?:fluent|fluency|proficien\w*|languages?|speaking|spoken|speak|bilingual|trilingual|"
            r"english|mandarin|chinese|french|spanish|german|japanese|korean|hindi|malay|tamil|arabic)\b", clause)
        if language and not re.search(r"\b(?:programming|coding)\s+languages?\b", clause):
            if mixed or (not soft and not negated):
                review.append("language_requirement_ambiguous")

    if "ambiguous" in sponsorship or {"unavailable", "available"} <= sponsorship:
        review.append("sponsorship_ambiguous")
    elif "unavailable" in sponsorship:
        required = fact("sponsorship_required")
        if required is None:
            review.append("sponsorship_required_unknown")
        elif required is True:
            excluded.append("sponsorship_unavailable")

    return {
        "decision": "excluded" if excluded else "needs_review" if review else "eligible",
        "reason_codes": sorted(set(excluded + review)),
        "used_facts": used,
    }


def confirmed_facts(bank, listing, now):
    """Select only exact, fresh, scoped saved boolean facts from the answer bank."""
    import answers
    if not isinstance(bank, dict) or not isinstance(bank.get('answers', []), list):
        raise EligibilityError('answer bank must contain an answers array')
    result = {}
    actual = {key: answers.normalize(str(listing[key])) for key in ('application_id', 'company', 'country') if listing.get(key)}
    for identity in ('sponsorship_required', 'legally_authorized_to_work', 'credit_bearing'):
        matches = [entry for entry in bank.get('answers', []) if isinstance(entry, dict) and entry.get('id') == identity]
        if len(matches) != 1:
            continue
        entry = matches[0]
        if type(entry.get('value')) is not bool or entry.get('basis', 'saved_answer') != 'saved_answer':
            continue
        scope = answers._scope(entry.get('scope', {}))
        if identity != 'credit_bearing' and not scope.get('country'):
            continue
        if any(actual.get(key) != value for key, value in scope.items()):
            continue
        question = {'type': 'boolean', 'options': [], 'employer_preferred_option': None}
        value, reason = answers._value(entry, question, bank, now)
        if reason is None and type(value) is bool:
            result[identity] = {'value': value, 'current': True, 'confirmed': True}
    return result
