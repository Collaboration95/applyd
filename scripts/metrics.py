"""Private action telemetry and pure aggregate accounting; caller owns the writer."""
from collections import Counter
import math
import re
from statistics import median
from zoneinfo import ZoneInfo

import batches
import budgets
import retry_policy
import settings

STAGES = frozenset({'discover', 'prepare', 'review', 'submit'})
REASONS = frozenset(reason.value for reason in retry_policy.Reason)
_FIELDS = {'sequence', 'application_id', 'stage', 'reserved_at', 'status',
           'duration_seconds', 'retry_reason', 'retry_count', 'failure_reason',
           'answer_reuse', 'usage', 'disposition'}


def _number(value, integer=False):
    return ((type(value) is int if integer else type(value) in (int, float))
            and value >= 0 and (type(value) is int or math.isfinite(value)))


def _sum(values):
    result = sum(values)
    if not _number(result):
        raise ValueError('Metrics total is outside the finite numeric range')
    return result


def _path(root, run_id):
    root = settings._root(root)
    settings._run_path(root, run_id)
    return settings._path(root, f'applications/runs/{run_id}/metrics.json')


def _validate(event):
    if not isinstance(event, dict) or set(event) != _FIELDS:
        raise ValueError('Invalid metrics event')
    if (not _number(event['sequence'], True) or event['sequence'] < 1
            or event['stage'] not in STAGES
            or event['status'] not in {'reserved', 'observed', 'failed'}
            or event['disposition'] not in {'pending', 'verified', 'not_executed', 'unresolved'}):
        raise ValueError('Invalid metrics action')
    app = event['application_id']
    if app is not None and (not isinstance(app, str) or not app):
        raise ValueError('Invalid metrics application identity')
    if (event['stage'] == 'discover') != (app is None):
        raise ValueError('Invalid metrics stage identity')
    batches.validate_event_time(event['reserved_at'])
    for field in ('duration_seconds', 'answer_reuse'):
        if event[field] is not None and not _number(event[field], field == 'answer_reuse'):
            raise ValueError('Invalid metrics measurement')
    for field in ('retry_reason', 'failure_reason'):
        if event[field] is not None and event[field] not in REASONS:
            raise ValueError('Invalid metrics reason')
    if (not _number(event['retry_count'], True)
            or (event['retry_reason'] is None) != (event['retry_count'] == 0)):
        raise ValueError('Invalid metrics retry count')
    budgets.validate_usage(event['usage'])
    if event['status'] == 'reserved' and any(event[k] is not None for k in
            ('duration_seconds', 'answer_reuse', 'usage', 'failure_reason')):
        raise ValueError('Reserved metrics cannot contain observations')
    return event


def _load(root, run_id):
    path = _path(root, run_id)
    if not path.exists():
        if path.is_symlink():
            raise ValueError('Invalid metrics path')
        return {'schema_version': 1, 'run_id': run_id, 'events': []}
    state = settings._read(path, 'metrics.json')
    if (not isinstance(state, dict) or set(state) != {'schema_version', 'run_id', 'events'}
            or type(state['schema_version']) is not int or state['schema_version'] != 1
            or state['run_id'] != run_id or not isinstance(state['events'], list)):
        raise ValueError('Invalid metrics ledger; preserve for reconciliation')
    previous = 0
    for event in state['events']:
        _validate(event)
        if event['sequence'] <= previous:
            raise ValueError('Metrics sequences cannot repeat')
        previous = event['sequence']
    return state


def _save(root, run_id, state):
    from workspace import atomic_json
    atomic_json(_path(root, run_id), state)


def reserve(root, run_id, sequence, stage, application_id, now, *, retry_reason=None, retry_count=0):
    """Persist before begin_submit/adapter. Never reuse a consumed sequence."""
    state = _load(root, run_id)
    event = _validate(dict(sequence=sequence, stage=stage, application_id=application_id,
        reserved_at=batches.validate_event_time(now).astimezone(ZoneInfo('Asia/Singapore')).isoformat(),
        status='reserved', duration_seconds=None, retry_reason=retry_reason, retry_count=retry_count,
        failure_reason=None, answer_reuse=None, usage=None, disposition='pending'))
    if state['events'] and sequence <= state['events'][-1]['sequence']:
        raise ValueError('Metrics reservation cannot be replayed')
    # A durable blocker may survive a successful prepare into review/submit.
    # Its same failure counter authorizes one retry, not three retry counts.
    if retry_reason is not None and any(e['application_id'] == application_id
            and e['retry_reason'] == retry_reason and e['retry_count'] >= retry_count
            for e in load_events(root)):
        event.update(retry_reason=None, retry_count=0)
    state['events'].append(event)
    _save(root, run_id, state)


def observe(root, run_id, sequence, duration_seconds, *, usage=None, failure_reason=None, failed=False):
    """Only same-process monotonic durations and Observation.usage are supplied."""
    state = _load(root, run_id)
    if not state['events'] or state['events'][-1]['sequence'] != sequence:
        raise ValueError('Metrics observation requires current reservation')
    event = state['events'][-1]
    changed = {**event, 'status': 'failed' if failed else 'observed',
               'duration_seconds': duration_seconds, 'usage': budgets.validate_usage(usage),
               'failure_reason': failure_reason}
    _validate(changed)
    if event['status'] != 'reserved' and changed != event:
        raise ValueError('Metrics observations cannot be replaced')
    state['events'][-1] = changed
    _save(root, run_id, state)


def reconcile(root, run_id, sequence, disposition, *, answer_reuse=None):
    """Update verification only; recovery never supplies elapsed time or usage."""
    state = _load(root, run_id)
    event = next((e for e in state['events'] if e['sequence'] == sequence), None)
    if event is None:  # Legacy pending work has no telemetry; never invent it.
        return
    changed = {**event, 'disposition': disposition}
    if answer_reuse is not None and event['status'] != 'reserved':
        if event['answer_reuse'] is not None and answer_reuse != event['answer_reuse']:
            raise ValueError('Answer reuse measurement cannot be replaced')
        changed['answer_reuse'] = answer_reuse
    _validate(changed)
    event.update(changed)
    _save(root, run_id, state)


def known_answer_reuse(rows):
    """Count actual resolved snapshot rows with a saved source, including false/0."""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Answer reuse requires snapshot rows')
    return sum(row.get('resolution') == 'resolved' and row.get('answer') is not None
               and isinstance(row.get('source_answer_id'), str) and bool(row['source_answer_id'])
               for row in rows)


def load_events(root):
    """Read only separate run ledgers; absent legacy telemetry returns no events."""
    root = settings._root(root)
    directory = settings._path(root, 'applications/runs')
    result = []
    for path in sorted(directory.glob('*/metrics.json')):
        state = _load(root, path.parent.name)
        result.extend({'run_id': state['run_id'], **event} for event in state['events'])
    return result


def _events(events):
    result, seen = [], set()
    for event in events:
        if not isinstance(event, dict) or set(event) != _FIELDS | {'run_id'}:
            raise ValueError('Expected loaded metrics events')
        # Validate run IDs without reading any files.
        run_id = event['run_id']
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', run_id):
            raise ValueError('Invalid metrics run identity')
        _validate({k: v for k, v in event.items() if k != 'run_id'})
        identity = (run_id, event['sequence'])
        if identity in seen:
            raise ValueError('Duplicate metrics observation')
        seen.add(identity)
        result.append(event)
    return sorted(result, key=lambda e: (e['run_id'], e['sequence']))


def effort_seconds(group, events, confirmed_at):
    """Complete measured adapter effort up to first confirmation, or unknown.

    All three application stages must have verified observations; every reserved
    action for this identity before confirmation must have a measured duration.
    Discovery is shared run work and is never allocated to an individual job.
    """
    ids = {r['application_id'] for r in group}
    selected = sorted((e for e in events if e['application_id'] in ids
                and batches.validate_event_time(e['reserved_at']) <= confirmed_at),
                key=lambda e: (e['run_id'], e['sequence']))
    stages = {e['stage'] for e in selected if e['disposition'] == 'verified' and e['status'] == 'observed'}
    if not {'prepare', 'review', 'submit'} <= stages or any(e['duration_seconds'] is None for e in selected):
        return None
    return _sum(e['duration_seconds'] for e in selected)


def summarize(records, events, *, start=None, end=None):
    """Pure aggregate-only API: fixed labels/numbers; no source IDs or content.

    Optional aware [start, end) selects first confirmations and reservations.
    Cohort effort includes earlier measured actions up to first confirmation.
    Provider totals require every selected reservation to report that metric;
    partial subtotals have explicit coverage.
    """
    records, events = list(records), _events(events)
    start = batches.validate_event_time(start) if start is not None else None
    end = batches.validate_event_time(end) if end is not None else None
    if start is not None and end is not None and start >= end:
        raise ValueError('Metrics interval must have positive length')

    def in_period(at):
        return (start is None or start <= at) and (end is None or at < end)

    confirmed, complete, partial, unknown = 0, [], 0, 0
    for group in batches._groups(records):
        confirmations = [at for r in group for at, _, _ in batches._confirmations(r)]
        if not confirmations:
            continue
        at = min(confirmations)
        if not in_period(at):
            continue
        confirmed += 1
        value = effort_seconds(group, events, at)
        if value is not None:
            complete.append(value)
        elif any(e['application_id'] in {r['application_id'] for r in group}
                 and batches.validate_event_time(e['reserved_at']) <= at
                 and e['duration_seconds'] is not None for e in events):
            partial += 1
        else:
            unknown += 1
    events = [e for e in events if in_period(batches.validate_event_time(e['reserved_at']))]
    stage_rows = []
    for stage in sorted(STAGES):
        selected = [e for e in events if e['stage'] == stage]
        values = [e['duration_seconds'] for e in selected if e['duration_seconds'] is not None]
        stage_rows.append({'stage': stage, 'reserved_actions': len(selected), 'measured_actions': len(values),
                           'measured_seconds': _sum(values) if values else None})
    usage, coverage = {}, {'reserved_actions': len(events)}
    for key in ('tokens', 'cost'):
        values = [(e['usage'] or {}).get(key) for e in events]
        measured = [v for v in values if v is not None]
        usage[key] = _sum(measured) if values and len(measured) == len(values) else None
        coverage[f'measured_{key}_actions'] = len(measured)
        coverage[f'measured_{key}'] = _sum(measured) if measured else None
    retries = Counter(e['retry_reason'] for e in events if e['retry_reason'] is not None)
    failures = Counter(e['failure_reason'] for e in events if e['failure_reason'] is not None)
    preparations = [e for e in events if e['stage'] == 'prepare']
    answers = [e['answer_reuse'] for e in preparations if e['answer_reuse'] is not None]
    return {'reserved_actions': len(events),
            'observed_actions': sum(e['status'] != 'reserved' for e in events),
            'incomplete_actions': sum(e['duration_seconds'] is None or e['disposition'] in {'pending', 'unresolved'} for e in events),
            'reserved_applications': len({e['application_id'] for e in events if e['application_id']}),
            'observed_attempted_applications': len({e['application_id'] for e in events if e['application_id'] and e['status'] != 'reserved'}),
            'unique_confirmed_applications': confirmed,
            'confirmed_with_complete_effort': len(complete), 'confirmed_with_partial_effort': partial,
            'confirmed_without_effort': unknown, 'median_effort_seconds': median(complete) if complete else None,
            'stage_durations': stage_rows,
            'retries': [{'reason': reason, 'count': count} for reason, count in sorted(retries.items())],
            'failures': [{'reason': reason, 'count': count} for reason, count in sorted(failures.items())],
            'answer_reuse': {'resolved_rows': _sum(answers) if answers else None,
                             'measured_preparations': len(answers), 'unmeasured_preparations': len(preparations) - len(answers)},
            'provider_usage': usage, 'provider_coverage': coverage}
