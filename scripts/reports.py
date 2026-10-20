"""Deterministic, local weekly application reporting over supplied records."""

import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from statistics import median
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import batches
import outcomes
import metrics as telemetry


_TYPED_STOP_REASONS = frozenset({
    "account_safety", "answer_validation", "browser_unavailable", "duplicate",
    "login_required", "page_changed", "rate_limited", "submission_uncertain",
    "timeout", "unknown_answer", "unsupported_form", "off_scope",
    "skipped:blocklist", "skipped:location", "skipped:required_years",
    "skipped:sponsorship", "skipped:credit_bearing", "review_mismatch",
}) | telemetry.REASONS
_PRESETS = frozenset({"software-backend", "ai-agents", "data-cloud"})
_ROLE_TAGS = frozenset({
    "software", "backend", "frontend", "full-stack", "data", "cloud", "ai",
    "internship", "full-time", "part-time", "engineering", "product", "it",
})


def _timestamp(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO timestamp with a UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp with a UTC offset") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed


def _week_bounds(week, now, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    if isinstance(week, datetime):
        raise ValueError("week must be a Monday ISO date or an ISO week such as 2026-W42")
    if isinstance(week, date):
        start_date = week
    elif isinstance(week, str):
        try:
            if re.fullmatch(r"[0-9]{4}-W[0-9]{2}", week):
                year, week_number = int(week[:4]), int(week[6:])
                start_date = date.fromisocalendar(year, week_number, 1)
            else:
                start_date = date.fromisoformat(week)
        except (ValueError, TypeError) as exc:
            raise ValueError("week must be a Monday ISO date or an ISO week such as 2026-W42") from exc
    else:
        raise ValueError("week must be a Monday ISO date or an ISO week such as 2026-W42")
    if start_date.weekday() != 0:
        raise ValueError("week date must be a Monday")
    if now is not None:
        current = now if isinstance(now, datetime) else _timestamp(now, "now")
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must include a timezone")
        current = current.astimezone(zone)
    else:
        current = datetime.now(zone)
    start = datetime.combine(start_date, time.min, tzinfo=zone)
    return start, start + timedelta(days=7), zone, current


def _event_times(record):
    """Yield only outcome occurrence times, never when a record was edited."""
    if not outcomes.responded(record):
        return []
    found = []
    events = record.get("outcome_events", [])
    if not isinstance(events, list):
        raise ValueError("outcome_events must be an array")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("outcome_events must contain objects")
        if event.get("status") in outcomes.RESPONSE_STATUSES:
            value = event.get("occurred_at") or event.get("at")
            if value:
                found.append(_timestamp(value, "outcome occurred_at"))
    if not found and outcomes.responded(record):
        value = record.get("latest_outcome_at") or record.get("outcome_at")
        if value:
            found.append(_timestamp(value, "latest_outcome_at"))
    return found


def _confirmed_events(records):
    """Map batches' cross-post identity groups to evidence-backed submissions."""
    result = []
    for group in batches._groups(records):
        confirmations = [(at, record) for record in group
                         for at, _batch, _category in batches._confirmations(record)]
        if confirmations:
            result.append((group, sorted(confirmations, key=lambda item: item[0])))
    return result


def _submission_context(group, confirmations):
    # Attribute a unique job to its first confirmed submission, with a stable
    # application_id tiebreaker when duplicate/cross-post records share a time.
    first_at = confirmations[0][0]
    candidates = [record for at, record in confirmations if at == first_at]
    candidates.sort(key=lambda record: record["application_id"])
    record = candidates[0]
    submission = next((item for item in record.get("submissions", [])
                       if item.get("status", "submitted") == "submitted" and item.get("submitted_at") and
                       _timestamp(item.get("occurred_at") or item["submitted_at"], "submitted_at") == first_at and
                       (item.get("confirmation_path") or item.get("evidence"))), None)
    prepared = (submission or {}).get("prepared") or record.get("prepared") or {}
    context = {**record, **prepared, **(submission or {})}
    context["_source_record"] = record
    context["_submitted_at"] = first_at
    return context


def _channel(record):
    explicit = record.get("channel")
    if explicit in {"easy_apply", "external"}:
        return explicit
    if record.get("easy_apply") is True or record.get("is_easy_apply") is True:
        return "easy_apply"
    if record.get("easy_apply") is False or record.get("is_easy_apply") is False:
        return "external"
    apply_url = record.get("application_url")
    listing_url = record.get("canonical_listing_url") or record.get("listing_url")
    if apply_url and listing_url:
        apply_host = (urlsplit(apply_url).hostname or "").casefold()
        listing_host = (urlsplit(listing_url).hostname or "").casefold()
        if apply_host and listing_host and apply_host != listing_host:
            return "external"
    return "unknown"


def _duration_seconds(group, context):
    # Consume only explicit measured fields. No provider usage or stage-cost
    # assumptions are converted into elapsed time.
    source = context.get("_source_record", {})
    candidates = [context, source]
    for item in candidates:
        if "effort_seconds" in item:
            value = item["effort_seconds"]
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("effort_seconds must be a non-negative number or null")
            return float(value)
        stages = item.get("stage_durations_seconds")
        if stages is None:
            stages = item.get("stage_durations")
        if stages is not None:
            if not isinstance(stages, dict):
                raise ValueError("stage durations must be an object of seconds")
            values = []
            for value in stages.values():
                if isinstance(value, dict):
                    value = value.get("duration_seconds")
                if value is None:
                    continue
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("stage durations must be non-negative seconds or null")
                values.append(float(value))
            if values:
                return sum(values)
    # A unique application can have multiple duplicate records; use one
    # explicitly measured duration at most, avoiding cross-post double count.
    for record in sorted(group, key=lambda item: item["application_id"]):
        if record is source:
            continue
        value = record.get("effort_seconds")
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return float(value)
        stages = record.get("stage_durations_seconds")
        if isinstance(stages, dict):
            vals = [v for v in stages.values() if type(v) in (int, float) and math.isfinite(v) and v >= 0]
            if vals:
                return float(sum(vals))
    return None


def _stop_reason_counts(records, start, end, zone):
    counts = Counter()
    for group in batches._groups(records):
        seen = set()
        for record in group:
            events = record.get("events", [])
            if not isinstance(events, list):
                raise ValueError("events must be an array")
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError("events must contain objects")
                reason = event.get("stop_reason") or event.get("reason")
                occurred = event.get("occurred_at") or event.get("at")
                if not reason or not occurred:
                    continue
                at = _timestamp(occurred, "event occurred_at").astimezone(zone)
                if not start <= at < end:
                    continue
                code = reason if reason in _TYPED_STOP_REASONS else "other"
                seen.add((code, at.isoformat()))
        for code, _at in seen:
            counts[code] += 1
    return [{"code": code, "count": count} for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


def _tags(record):
    values = record.get("role_tags", record.get("tags", [])) or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("role_tags must be a string or array of strings")
    return sorted({value.strip() for value in values if isinstance(value, str) and value.strip()}) or ["unknown"]


def _rate(responses, applications):
    return round(responses / applications, 4) if applications else None


def _breakdown(rows, dimension):
    counts = {}
    for row in rows:
        for label in row[dimension]:
            item = counts.setdefault(label, [0, 0])
            item[0] += 1
            item[1] += int(row["responded_in_week"])
    return [{"group": label, "applications": app_count, "responses": response_count,
             "response_rate": _rate(response_count, app_count)}
            for label, (app_count, response_count) in sorted(counts.items())]


def _shareable(report):
    """Remove user-provided labels and identifiers; retain static aggregate groups."""
    result = {key: value for key, value in report.items() if key != "pending_questions"}
    result["breakdowns"] = {}
    for dimension, rows in report["breakdowns"].items():
        normalized = {}
        for row in rows:
            label = row["group"]
            if dimension == "preset":
                label = label if label in _PRESETS else "other"
            elif dimension == "resume_scope":
                label = "master" if label == "master" else ("other" if label == "unknown" else "role_specific")
            elif dimension == "role_tag":
                label = label.casefold() if label.casefold() in _ROLE_TAGS else "other"
            elif dimension == "channel":
                label = label if label in {"easy_apply", "external"} else "unknown"
            counts = normalized.setdefault(label, [0, 0])
            counts[0] += row["applications"]
            counts[1] += row["responses"]
        result["breakdowns"][dimension] = [
            {"group": label, "applications": counts[0], "responses": counts[1],
             "response_rate": _rate(counts[1], counts[0])}
            for label, counts in sorted(normalized.items())
        ]
    reasons = Counter()
    for item in report["stop_reasons"]:
        code = item["code"] if item["code"] in _TYPED_STOP_REASONS else "other"
        reasons[code] += item["count"]
    result["stop_reasons"] = [{"code": code, "count": count} for code, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))]
    result["pending_questions"] = {"applications": report["pending_questions"]["applications"],
                                   "question_count": report["pending_questions"]["question_count"]}
    result["shareable"] = True
    return result


def weekly_report(records, week, now=None, timezone="UTC", pending_questions=None, shareable=False, metrics=None):
    """Build a deterministic report for an ISO week from caller-supplied records.

    Application volume is the unique set with a confirmation occurrence in the
    week. Responses count unique applications whose response occurrence is in
    the same week. Rates divide same-week responses by same-week applications.
    """
    if not isinstance(records, (list, tuple)):
        records = list(records)
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("records must contain application objects")
    start, end, zone, current = _week_bounds(week, now, timezone)
    metrics = list(metrics) if metrics is not None else None
    measurement = telemetry.summarize(records, metrics, start=start, end=end) if metrics is not None else None
    local_start, local_end = start.isoformat(), end.isoformat()
    confirmed = _confirmed_events(records)
    rows, response_ids = [], set()
    pending_by_id = {}
    for group, confirmations in confirmed:
        context = _submission_context(group, confirmations)
        app_id = context["application_id"]
        # The first evidence-backed confirmation defines the unique application's
        # reporting period; later duplicate attempts do not move it to another week.
        app_in_week = start <= confirmations[0][0].astimezone(zone) < end
        if app_in_week:
            response_in_week = any(start <= at.astimezone(zone) < end
                                    for record in group for at in _event_times(record))
            row = {"_id": app_id, "resume_scope": [context.get("scope") or context.get("resume_scope") or "unknown"],
                   "preset": [context.get("preset") or context.get("resume_preset") or "unknown"],
                   "role_tag": _tags(context), "channel": [_channel(context)],
                   "responded_in_week": response_in_week,
                   "effort_seconds": (_duration_seconds(group, context) if metrics is None else
                                      telemetry.effort_seconds(group, metrics, confirmations[0][0]))}
            rows.append(row)
            if response_in_week:
                response_ids.add(app_id)
    # Pending questions describe the current inbox snapshot, not a historical
    # application event. Keep the actual durable IDs in the local report.
    if pending_questions is None:
        pending_source = [{"application_id": record.get("application_id"),
                           "blocked_on_answer_ids": record.get("blocked_on_answer_ids") or []}
                          for record in records]
    elif isinstance(pending_questions, dict):
        pending_source = [{"application_id": app_id, "blocked_on_answer_ids": ids}
                          for app_id, ids in pending_questions.items()]
    else:
        pending_source = list(pending_questions)
    for item in pending_source:
        if not isinstance(item, dict):
            raise ValueError("pending_questions must contain application/question ID objects")
        app_id = item.get("application_id")
        blockers = item.get("blocked_on_answer_ids", item.get("question_ids", [])) or []
        if blockers:
            if not isinstance(app_id, str) or not app_id:
                raise ValueError("blocked records require an application_id")
            if isinstance(blockers, str):
                blockers = [blockers]
            pending_by_id.setdefault(app_id, set()).update(str(value) for value in blockers)
    pending = [{"application_id": app_id, "question_ids": sorted(question_ids)}
               for app_id, question_ids in sorted(pending_by_id.items())]
    app_ids = {row["_id"] for row in rows}
    effort = [row["effort_seconds"] for row in rows if row["effort_seconds"] is not None]
    report = {
        "schema_version": 1,
        "period": {"week": f"{start.isocalendar().year}-W{start.isocalendar().week:02d}",
                   "start": local_start, "end_exclusive": local_end, "timezone": timezone},
        "totals": {"applications": len(app_ids), "responses": len(response_ids),
                   "response_rate": _rate(len(response_ids), len(app_ids)),
                   "median_effort_seconds": median(effort) if effort else None,
                   "measured_effort_applications": len(effort),
                   "provider_usage": measurement["provider_usage"] if measurement is not None else None},
        "breakdowns": {"resume_scope": _breakdown(rows, "resume_scope"),
                       "preset": _breakdown(rows, "preset"),
                       "role_tag": _breakdown(rows, "role_tag"),
                       "channel": _breakdown(rows, "channel")},
        "stop_reasons": _stop_reason_counts(records, start, end, zone),
        "pending_questions": {"applications": len({item["application_id"] for item in pending}),
                              "question_count": sum(len(item["question_ids"]) for item in pending),
                              "blocked": sorted(pending, key=lambda item: item["application_id"])},
        "shareable": False,
    }
    if measurement is not None:
        report["metrics"] = measurement
    return _shareable(report) if shareable else report


def format_report(report):
    """Render a compact one-screen text report without private labels in shareable mode."""
    period = report["period"]
    totals = report["totals"]
    median_value = totals.get("median_effort_seconds")
    median_text = "Unavailable" if median_value is None else f"{median_value:g} s"
    rate = totals.get("response_rate")
    rate_text = "Unavailable" if rate is None else f"{rate:.1%}"
    lines = [f"Weekly report · {period['week']} · {period['timezone']}",
             f"Applications: {totals['applications']}  Responses: {totals['responses']}  Rate: {rate_text}",
             f"Median measured effort: {median_text} ({totals['measured_effort_applications']} measured)  Provider usage: Unavailable"]
    measurement = report.get("metrics")
    if measurement is not None:
        coverage = measurement['provider_coverage']
        parts = []
        for key in ('tokens', 'cost'):
            total = measurement['provider_usage'][key]
            count = coverage[f'measured_{key}_actions']
            subtotal = coverage[f'measured_{key}']
            value = 'Unavailable' if subtotal is None else f'{subtotal:g}'
            parts.append(f'{key}: {total:g}' if total is not None else
                         f'{key}: total unavailable; measured subtotal {value} ({count}/{coverage["reserved_actions"]} actions)')
        lines[2] = (f'Median measured effort: {median_text} '
                    f'({totals["measured_effort_applications"]}/{totals["applications"]} confirmed applications with complete stage coverage)')
        lines.append(f'Recorded action provider usage ({coverage["reserved_actions"]} actions): ' + '; '.join(parts))
        lines.append(f'Reserved applications: {measurement["reserved_applications"]}  '
                     f'Observed attempts: {measurement["observed_attempted_applications"]}  '
                     f'Incomplete actions: {measurement["incomplete_actions"]}')
    for dimension, title in (("preset", "Preset"), ("resume_scope", "Resume scope"),
                             ("role_tag", "Role tag"), ("channel", "Channel")):
        groups = report["breakdowns"].get(dimension, [])
        parts = []
        for item in groups:
            group_rate = "—" if item["response_rate"] is None else f"{item['response_rate']:.0%}"
            parts.append(f"{item['group']} {item['applications']}/{item['responses']} ({group_rate})")
        lines.append(f"{title}: " + (", ".join(parts) if parts else "No applications"))
    reasons = report.get("stop_reasons", [])
    lines.append("Stop reasons: " + (", ".join(f"{item['code']} {item['count']}" for item in reasons) or "None"))
    questions = report.get("pending_questions", {})
    lines.append(f"Pending questions: {questions.get('applications', 0)} applications, {questions.get('question_count', 0)} questions")
    if not report.get("shareable"):
        for item in questions.get("blocked", []):
            lines.append(f"  {item['application_id']}: {', '.join(item['question_ids'])}")
    return "\n".join(lines)
