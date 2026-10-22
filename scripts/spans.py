"""Versioned private spans; durable reservations, same-process monotonic timing.

The workspace writer owns all ledger changes (including imported tool spans).
Telemetry failure is sticky and never retries the measured operation.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo
import math
import time
import uuid

import batches
import settings

PHASES = frozenset('workflow startup context_loading saved_discovery card_screening navigation listing_capture eligibility answer_resolution resume_resolution resume_reuse resume_upload file_picker form_observation form_filling form_readback review preflight submission confirmation persistence export retry handoff orchestration'.split())
CHANNELS = frozenset({'browser', 'model', 'tool', 'user_wait', 'blocked_wait', 'orchestration'})
COUNTERS = frozenset({'uploads', 'repeated_uploads', 'skipped_fields', 'field_writes', 'record_reads', 'exports', 'observations', 'redundant_observations', 'retries'})
CURRENT = ContextVar('applyd_spans', default=None)
AUTHORIZING = ContextVar('applyd_span_authorization', default=False)
FIELDS = frozenset({'schema_version', 'run_id', 'application_id', 'attempt_id', 'action_id', 'span_id', 'parent_id', 'process_id', 'phase', 'channel', 'started_at', 'ended_at', 'duration_seconds', 'outcome', 'coverage', 'counters', 'model'})


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate(row):
    if not isinstance(row, dict) or set(row) != FIELDS or row['schema_version'] != 2:
        raise ValueError('invalid_span_schema')
    for key in ('run_id', 'span_id', 'process_id'):
        if not isinstance(row[key], str) or not row[key] or len(row[key]) > 128:
            raise ValueError('invalid_span_identity')
    for key in ('application_id', 'attempt_id', 'action_id', 'parent_id'):
        if row[key] is not None and (not isinstance(row[key], str) or not row[key] or len(row[key]) > 128):
            raise ValueError('invalid_span_correlation')
    if row['phase'] not in PHASES or row['channel'] not in CHANNELS or row['outcome'] not in {'open', 'ok', 'failed', 'handoff'} or row['coverage'] not in {'open', 'measured', 'unknown'}:
        raise ValueError('invalid_span_label')
    batches.validate_event_time(row['started_at'])
    if row['ended_at'] is not None:
        batches.validate_event_time(row['ended_at'])
    if row['outcome'] == 'open':
        if row['ended_at'] is not None or row['duration_seconds'] is not None or row['coverage'] != 'open':
            raise ValueError('invalid_open_span')
    elif row['coverage'] == 'measured':
        if row['ended_at'] is None or not _finite(row['duration_seconds']):
            raise ValueError('invalid_span_duration')
    elif row['duration_seconds'] is not None:
        raise ValueError('unknown_span_duration')
    if not isinstance(row['counters'], dict) or any(k not in COUNTERS or type(v) is not int or v < 0 for k, v in row['counters'].items()):
        raise ValueError('invalid_span_counters')
    model = row['model']
    if model is not None:
        if (not isinstance(model, dict) or set(model) != {'provider', 'model', 'configuration', 'output_tokens', 'generation_seconds'}
                or any(not isinstance(model[k], str) or len(model[k]) > 128 for k in ('provider', 'model', 'configuration'))
                or (model['output_tokens'] is not None and (type(model['output_tokens']) is not int or model['output_tokens'] < 0))
                or (model['generation_seconds'] is not None and not _finite(model['generation_seconds']))):
            raise ValueError('invalid_span_model_measurement')
        if model['generation_seconds'] is not None and (row['channel'] != 'model' or row['coverage'] != 'measured' or model['generation_seconds'] > row['duration_seconds'] + .000001):
            raise ValueError('model_generation_interval_mismatch')
    return row


def _path(root, run_id):
    root = settings._root(root)
    settings._run_path(root, run_id)
    return settings._path(settings._root(root), f'applications/runs/{run_id}/spans.json')


def load(root, run_id=None):
    paths = [_path(root, run_id)] if run_id else sorted(settings._path(settings._root(root), 'applications/runs').glob('*/spans.json'))
    rows, seen = [], set()
    for path in paths:
        path = _path(root, path.parent.name)
        if not path.exists():
            if path.is_symlink():
                raise ValueError('invalid_span_path')
            continue
        state = settings._read(path, 'spans.json')
        if not isinstance(state, dict) or set(state) != {'schema_version', 'spans'} or state['schema_version'] != 2 or not isinstance(state['spans'], list):
            raise ValueError('invalid_span_ledger')
        for row in state['spans']:
            validate(row)
            if row['run_id'] != path.parent.name or (row['run_id'], row['span_id']) in seen:
                raise ValueError('duplicate_span')
            seen.add((row['run_id'], row['span_id'])); rows.append(row)
    return rows


class Recorder:
    def __init__(self, root, run_id, *, monotonic=None, wall=None):
        self.root, self.run_id = root, run_id
        self.monotonic = monotonic or time.monotonic
        self.wall = wall or (lambda: datetime.now(ZoneInfo('Asia/Singapore')))
        self.process_id = uuid.uuid4().hex
        self.stack, self.rows, self.available = [], None, True
        self.application_id = self.attempt_id = self.action_id = None

    def _save(self):
        from workspace import atomic_json
        latest = {(r['run_id'], r['span_id']): r for r in load(self.root, self.run_id)}
        for row in self.rows:
            key = (row['run_id'], row['span_id'])
            if key in latest and latest[key]['outcome'] != 'open' and row['outcome'] == 'open':
                continue
            latest[key] = row
        atomic_json(_path(self.root, self.run_id), {'schema_version': 2, 'spans': list(latest.values())})

    def _safe(self, fn):
        if not self.available:
            return None
        try:
            return fn()
        except Exception:
            self.available = False
            return None

    def correlate(self, action, attempt_id=None):
        self.application_id, self.action_id = action.application_id, str(action.sequence)
        self.attempt_id = attempt_id

    @contextmanager
    def span(self, phase, channel='tool', *, counters=None, model=None, writer=None):
        if AUTHORIZING.get():
            yield None
            return
        row = None
        def begin():
            nonlocal row
            if self.rows is None:
                self.rows = load(self.root, self.run_id)
            started = self.monotonic()
            row = validate(dict(schema_version=2, run_id=self.run_id, application_id=self.application_id,
                attempt_id=self.attempt_id, action_id=self.action_id, span_id=uuid.uuid4().hex,
                parent_id=self.stack[-1]['span_id'] if self.stack else None, process_id=self.process_id,
                phase=phase, channel=channel, started_at=batches.validate_event_time(self.wall()).astimezone(ZoneInfo('Asia/Singapore')).isoformat(),
                ended_at=None, duration_seconds=None, outcome='open', coverage='open', counters=counters or {}, model=model))
            self.rows.append(row); self._save()
            return started
        with writer() if writer else nullcontext():
            started = self._safe(begin)
        token = CURRENT.set(self)
        if row is not None:
            self.stack.append(row)
        outcome = 'ok'
        try:
            yield row
        except BaseException as exc:
            outcome = 'failed' if isinstance(exc, Exception) else 'open'
            raise
        finally:
            if row is not None:
                self.stack.pop()
            CURRENT.reset(token)
            if outcome != 'open' and started is not None:
                def finish():
                    row.update(ended_at=batches.validate_event_time(self.wall()).astimezone(ZoneInfo('Asia/Singapore')).isoformat(),
                        duration_seconds=self.monotonic() - started, outcome=row['outcome'] if row['outcome'] in {'failed','handoff'} else outcome, coverage='measured')
                    validate(row); self._save()
                with writer() if writer else nullcontext():
                    self._safe(finish)

    def call(self, phase, channel, fn, **kwargs):
        with self.span(phase, channel, **kwargs):
            return fn()

    def generation(self, phase, configuration, fn, *, output_usage=None, before_call=None):
        """fn returns (value, actual output_tokens or None) from its provider.

        No model label, service tier, input-token count or advertised mode can
        supply output usage. Time only the provider callback, excluding durable
        reservation, fresh guards and subsequent usage extraction.
        """
        model = {**configuration, 'output_tokens': None, 'generation_seconds': None}
        with self.span(phase, 'model', model=model) as row:
            if before_call is not None:
                before_call()
            started = self.monotonic()
            result = fn()
            generation_seconds = self.monotonic() - started
            if output_usage is None:
                value, output_tokens = result
            else:
                value = result
                try:
                    output_tokens = output_usage(result)
                except Exception:
                    output_tokens = None
            if output_tokens is not None and (type(output_tokens) is not int or output_tokens < 0):
                output_tokens = None  # malformed telemetry cannot change a provider result
            if row is not None:
                row['model']['output_tokens'] = output_tokens
                if output_tokens is not None and _finite(generation_seconds):
                    row['model']['generation_seconds'] = generation_seconds
            return value

    def ingest(self, row):
        """Trusted tool transport supplies the same contract; never execute/replay."""
        def append():
            validate(row)
            if self.rows is None:
                self.rows = load(self.root, self.run_id)
            if row['run_id'] != self.run_id:
                raise ValueError('foreign_span')
            previous = next((r for r in self.rows if r['span_id'] == row['span_id']), None)
            if previous is None and row['outcome'] != 'open':
                raise ValueError('span_reservation_required')
            if previous is not None:
                fixed = FIELDS - {'ended_at', 'duration_seconds', 'outcome', 'coverage', 'model', 'counters'}
                if previous['outcome'] != 'open' or row['outcome'] == 'open' or any(previous[k] != row[k] for k in fixed):
                    raise ValueError('duplicate_or_changed_span')
                if (set(previous['counters']) != set(row['counters']) or any(row['counters'][k] < v for k, v in previous['counters'].items())):
                    raise ValueError('changed_span_counters')
                before_model, after_model = previous['model'], row['model']
                if (before_model is None) != (after_model is None) or (before_model is not None and (any(before_model[k] != after_model[k] for k in ('provider','model','configuration')) or any(before_model[k] is not None for k in ('output_tokens','generation_seconds')))):
                    raise ValueError('changed_span_model')
                self.rows.remove(previous)
            if row['parent_id'] is not None and not any(r['span_id'] == row['parent_id'] for r in self.rows):
                raise ValueError('missing_span_parent')
            self.rows.append(dict(row)); self._save()
        self._safe(append)
        return self.available


def count(name, amount=1):
    """Count actual attempted helper operations in the current measured boundary.

    Persisted with that boundary's completion; interrupted counters stay unknown.
    No extra ledger write or browser/tool round trip is needed per record read.
    """
    recorder = CURRENT.get()
    if recorder is not None and recorder.available and recorder.stack:
        if name not in COUNTERS or type(amount) is not int or amount < 0:
            raise ValueError('invalid_span_counter')
        counters = recorder.stack[-1]['counters']
        counters[name] = counters.get(name, 0) + amount


@contextmanager
def span(phase, channel='tool', **kwargs):
    recorder = CURRENT.get()
    if recorder is None:
        yield None
    else:
        with recorder.span(phase, channel, **kwargs) as row:
            yield row


def measured(phase, channel='tool', **kwargs):
    """Instrument actual helper calls without changing return/exception behavior."""
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kw):
            with span(phase, channel, **kwargs):
                return fn(*args, **kw)
        return wrapped
    return decorate


def authorize(guard):
    """No telemetry writes inside a last-moment authorization callback.

    Its effort remains in the enclosing native span; a nested guard span's
    completion save must not create a fresh STOP window after the check.
    """
    token = AUTHORIZING.set(True)
    try:
        return guard()
    finally:
        AUTHORIZING.reset(token)


def require_native_guard(guard):
    if authorize(guard) is not None:
        raise ValueError('native_guard_refused')


class LiveAdapter:
    """Concrete synchronous boundary wrapper. Inner tool calls use boundary.call.

    perform(action, boundary) is optional; legacy __call__(action) remains valid
    and records only the observed outer browser boundary, no invented children.
    """
    def __init__(self, adapter, *, guard, invoke=None):
        self.adapter, self.guard = adapter, guard
        self.invoke = invoke or (lambda fn: fn())

    def __call__(self, action):
        recorder = CURRENT.get()
        phase = {'discover': 'saved_discovery', 'prepare': 'form_filling', 'review': 'review', 'submit': 'submission'}[action.kind.value]
        with span(phase, 'browser') as row:
            require_native_guard(self.guard)  # after reservation, before native work
            perform = getattr(type(self.adapter), 'perform', None)
            boundary = NativeBoundary(recorder, self.guard)
            result = self.invoke(lambda: perform(self.adapter, action, boundary) if callable(perform) else self.adapter(action))
            if row is not None and getattr(result, 'failure', None) is not None:
                row['outcome'] = 'failed'
            return result

    def observe_url(self, action):
        with span('preflight', 'browser'):
            require_native_guard(self.guard)
            return self.adapter.observe_url(action)


class NativeBoundary:
    """Keep each nested native callback guarded after its own reservation."""
    def __init__(self, recorder, guard):
        self.recorder, self.guard = recorder, guard

    def __getattr__(self, name):
        return getattr(self.recorder, name)

    def call(self, phase, channel, fn, **kwargs):
        counters = kwargs.pop('counters', None) or {}
        with span(phase, channel, counters=dict.fromkeys(counters, 0), **kwargs) as row:
            require_native_guard(self.guard)
            if row is not None:
                row['counters'].update(counters)
            return fn()

    def generation(self, phase, configuration, fn, **kwargs):
        return self.recorder.generation(phase, configuration, fn, before_call=lambda: require_native_guard(self.guard), **kwargs)


def quantiles(values):
    values = sorted(values)
    return {'samples': len(values), 'median_seconds': median(values) if values else None,
            'p95_seconds': values[math.ceil(.95 * len(values)) - 1] if values else None}


def _union(intervals):
    total, end = 0., None
    for a, b in sorted(intervals):
        if end is None or a >= end:
            total += (b-a).total_seconds()
        elif b > end:
            total += (b-end).total_seconds()
        end = max(end, b) if end else b
    return total


def summarize(records, rows, *, start=None, end=None):
    """Aggregate only, fixed labels. Inclusive phase samples are never totaled.

    Process effort uses non-nested completed roots per process. Active time is
    the union of monotonic-length measured intervals anchored on wall starts;
    wall-end agreement is required for overlap placement. Cross-process clock
    alignment is an explicit assumption, never a comparison of clock origins.
    """
    records, all_rows = list(records), list(rows)
    seen = set()
    for r in all_rows:
        validate(r)
        key = (r['run_id'], r['span_id'])
        if key in seen:
            raise ValueError('duplicate_span')
        seen.add(key)
    start = batches.validate_event_time(start) if start else None
    end = batches.validate_event_time(end) if end else None
    if start and end and start >= end:
        raise ValueError('invalid_span_period')
    def selected(at):
        return (start is None or at >= start) and (end is None or at < end)
    rows = [r for r in all_rows if selected(batches.validate_event_time(r['started_at']))]
    index = {(r['run_id'], r['span_id']): r for r in all_rows}
    orphaned = False
    for row in all_rows:
        visited = {(row['run_id'], row['span_id'])}
        while row['parent_id']:
            key = (row['run_id'], row['parent_id'])
            if key in visited: raise ValueError('cyclic_span')
            visited.add(key)
            if key not in index:
                orphaned = True; break
            row = index[key]
    def measured(r):
        return r['coverage'] == 'measured' and r['duration_seconds'] is not None
    def ancestor(r):
        walked, process_id = set(), r['process_id']
        while r['parent_id']:
            key = (r['run_id'], r['parent_id'])
            if key in walked:
                raise ValueError('cyclic_span')
            walked.add(key)
            parent = index.get(key)
            if parent is None:
                return False
            if parent['process_id'] == process_id and measured(parent) and parent in rows:
                return True
            r = parent
        return False
    roots = [r for r in rows if measured(r) and not ancestor(r)]
    intervals, aligned = [], True
    placed = {}
    for r in rows:
        if not measured(r):
            continue
        a = batches.validate_event_time(r['started_at']); b = a + timedelta(seconds=r['duration_seconds'])
        if abs((batches.validate_event_time(r['ended_at'])-b).total_seconds()) > .01:
            aligned = False
        placed[(r['run_id'], r['span_id'])] = (a, b)
        if r['channel'] not in {'user_wait', 'blocked_wait'}:
            intervals.append((a, b))
    # An inclusive caller is blocked by a descendant wait even across a tool
    # process boundary. Propagate only along that dependency chain, retaining
    # independently measured concurrent work; union all resulting fragments.
    excluded, unknown_wait = {}, False
    selected_keys = {(r['run_id'], r['span_id']) for r in rows}
    for wait in all_rows:
        if wait['channel'] not in {'user_wait', 'blocked_wait'}:
            continue
        ancestors, current = [], wait
        while current['parent_id']:
            key = (current['run_id'], current['parent_id'])
            if key not in index:
                break
            ancestors.append(key)
            current = index[key]
        # Selected inclusive samples keep their full durations. Their observed
        # dependencies must also apply when a child's start is outside the period.
        if (wait['run_id'], wait['span_id']) not in selected_keys and not selected_keys.intersection(ancestors):
            continue
        if not measured(wait):
            unknown_wait = True
            continue
        a = batches.validate_event_time(wait['started_at'])
        b = a + timedelta(seconds=wait['duration_seconds'])
        if abs((batches.validate_event_time(wait['ended_at'])-b).total_seconds()) > .01:
            aligned = False
        for key in ancestors:
            excluded.setdefault(key, []).append((a, b))
    active_intervals = [part for r in rows
                        if measured(r) and r['channel'] not in {'user_wait', 'blocked_wait'}
                        for part in _subtract([placed[(r['run_id'], r['span_id'])]],
                                              excluded.get((r['run_id'], r['span_id']), []))]
    active = _union(active_intervals)
    confirmations, confirmed_runs = [], []
    for group in batches._groups(records):
        times = [at for r in group for at, _, _ in batches._confirmations(r)]
        if times and selected(min(times)):
            first = min(times)
            confirmations.append(first)
            run_ids = {s.get('run_id') for record in group for s in record.get('submissions', [])
                       if s.get('submitted_at') and batches.validate_event_time(s.get('occurred_at') or s['submitted_at']) == first
                       and (s.get('confirmation_path') or s.get('evidence')) and s.get('status', 'submitted') == 'submitted'}
            confirmed_runs.append((first, run_ids))
    workflow = [r for r in rows if r['phase'] == 'workflow']
    begins = [batches.validate_event_time(r['started_at']) for r in workflow]
    ends = [batches.validate_event_time(r['ended_at']) for r in workflow if measured(r)]
    run_windows = {}
    for rid in {r['run_id'] for r in workflow}:
        group = [r for r in workflow if r['run_id']==rid]
        if all(measured(r) for r in group):
            a = min(batches.validate_event_time(r['started_at']) for r in group)
            b = max(batches.validate_event_time(r['ended_at']) for r in group)
            if b >= a: run_windows[rid] = (a,b)
    wall_seconds = sum((b-a).total_seconds() for a,b in run_windows.values()) if run_windows and len(ends)==len(begins) else None
    active_confirmations = [at for at, runs in confirmed_runs if any(r['run_id'] in runs and measured(r) and batches.validate_event_time(r['started_at']) <= at <= batches.validate_event_time(r['ended_at']) for r in workflow)]
    timed_confirmations = [at for at, runs in confirmed_runs if any(rid in run_windows and run_windows[rid][0] <= at <= run_windows[rid][1] for rid in runs)]
    active = max(0., active) if aligned and not orphaned and intervals and not unknown_wait else None
    counters = {k: sum(r['counters'].get(k, 0) for r in rows if measured(r)) if any(k in r['counters'] and measured(r) for r in rows) else None for k in sorted(COUNTERS)}
    counter_coverage = [{'counter':k, 'measured_spans':sum(k in r['counters'] and measured(r) for r in rows), 'unknown_spans':sum(k in r['counters'] and not measured(r) for r in rows)} for k in sorted(COUNTERS)]
    process_effort = sum(_union([(batches.validate_event_time(r['started_at']), batches.validate_event_time(r['started_at']) + timedelta(seconds=r['duration_seconds'])) for r in roots if r['process_id']==pid]) for pid in sorted({r['process_id'] for r in roots})) if aligned and roots else None
    failed = [r for r in rows if measured(r) and r['outcome']=='failed']
    failure_seconds = _union([(batches.validate_event_time(r['started_at']), batches.validate_event_time(r['started_at'])+timedelta(seconds=r['duration_seconds'])) for r in failed]) if aligned and failed else None
    model_rows = [r['model'] for r in rows if r['model'] is not None]
    exclusive = {channel: [] for channel in CHANNELS}
    exclusive_unknown = set()
    for r in rows:
        if not measured(r): continue
        # Selected parents retain their full duration, so all recorded direct
        # dependencies apply, including children starting outside this period.
        children = [c for c in all_rows if c['parent_id']==r['span_id'] and c['run_id']==r['run_id']]
        if any(not measured(c) for c in children):
            exclusive_unknown.add(r['channel']); continue
        a = batches.validate_event_time(r['started_at']); b = a + timedelta(seconds=r['duration_seconds'])
        occupied = [(batches.validate_event_time(c['started_at']), batches.validate_event_time(c['started_at'])+timedelta(seconds=c['duration_seconds'])) for c in children]
        exclusive[r['channel']].extend((r['process_id'], x, y) for x,y in _subtract([(a,b)], occupied))
    process_summaries = []
    for pid in {r['process_id'] for r in rows}:
        group = [r for r in rows if r['process_id']==pid]
        process_summaries.append({'measured_spans':sum(measured(r) for r in group), 'unknown_spans':sum(not measured(r) for r in group),
            'phases':[{'phase':phase, **quantiles([r['duration_seconds'] for r in group if measured(r) and r['phase']==phase])} for phase in sorted({r['phase'] for r in group})]})
    # Sort anonymous summaries by values, never expose arbitrary process labels.
    import json
    process_summaries.sort(key=lambda row: json.dumps(row, sort_keys=True))
    generations = [m for m in model_rows if m['output_tokens'] is not None and m['generation_seconds'] is not None and m['generation_seconds'] > 0]
    return {'schema_version': 2, 'spans': len(rows), 'measured_spans': sum(measured(r) for r in rows),
        'unknown_spans': sum(not measured(r) for r in rows), 'coverage': 'partial' if orphaned or any(not measured(r) for r in rows) or not workflow else 'recorded_intervals_only',
        'critical_path_active_seconds': active, 'summed_process_effort_seconds': process_effort,
        'wall_run_seconds': wall_seconds, 'unique_confirmed_applications': len(confirmations),
        'throughput_confirmations': len(active_confirmations), 'wall_throughput_confirmations': len(timed_confirmations),
        'confirmations_per_wall_hour': len(timed_confirmations)*3600/wall_seconds if wall_seconds and wall_seconds>0 else None,
        'confirmations_per_measured_active_hour': len(active_confirmations)*3600/active if active and active>0 else None,
        'time_to_first_confirmation_seconds': (min(timed_confirmations)-min(begins)).total_seconds() if timed_confirmations and begins and min(timed_confirmations)>=min(begins) else None,
        'phases': [{'phase': phase, **quantiles([r['duration_seconds'] for r in rows if r['phase']==phase and measured(r)])} for phase in sorted(PHASES)],
        'channels': [{'channel': channel, **quantiles([r['duration_seconds'] for r in rows if r['channel']==channel and measured(r)])} for channel in sorted(CHANNELS)],
        'processes': process_summaries,
        'exclusive_channels': [{'channel': channel, 'seconds': sum(_union([(a,b) for process,a,b in exclusive[channel] if process==pid]) for pid in sorted({process for process,_,_ in exclusive[channel]})) if aligned and exclusive[channel] and channel not in exclusive_unknown else None} for channel in sorted(CHANNELS)],
        'failure_seconds': failure_seconds,
        'retry_seconds': _union([(batches.validate_event_time(r['started_at']), batches.validate_event_time(r['started_at'])+timedelta(seconds=r['duration_seconds'])) for r in rows if measured(r) and r['phase']=='retry']) if aligned and any(r['phase']=='retry' and measured(r) for r in rows) else None,
        'counters': counters, 'counter_coverage': counter_coverage, 'model_measurements': len(model_rows), 'model_configurations': len({(m['provider'],m['model'],m['configuration']) for m in model_rows}), 'measured_generations': len(generations),
        'output_tokens_per_second': sum(m['output_tokens'] for m in generations)/sum(m['generation_seconds'] for m in generations) if generations else None,
        'clock_alignment': 'wall_placement_with_monotonic_lengths' if aligned else 'unknown',
        'quantile_rule': 'median_midpoint_p95_nearest_rank'}


def _merge(intervals):
    result = []
    for a,b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(b,result[-1][1]))
        else:
            result.append((a,b))
    return result



def _subtract(intervals, excluded):
    """Interval difference; no nesting or concurrent sibling double counts."""
    fragments = _merge(intervals)
    for a,b in _merge(excluded):
        next_fragments = []
        for c,d in fragments:
            if b <= c or a >= d:
                next_fragments.append((c,d))
            else:
                if c < a: next_fragments.append((c,a))
                if b < d: next_fragments.append((b,d))
        fragments = next_fragments
    return fragments
