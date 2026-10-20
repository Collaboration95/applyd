"""Synthetic temporary ledgers, fake monotonic clocks and privacy canaries."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import metrics
import retry_policy
import runner as r
import settings
import workspace as w
from runner_workspace import build_runner
from test_runner import Parent, NOW, job, confirmation
import test_runner_workspace as parent_fixtures


class FakeMonotonic:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def measured(root, sequence, stage, app='one', duration=2, *, run_id='run-test',
             usage=None, now=NOW, disposition='verified', reuse=None, **retry):
    metrics.reserve(root, run_id, sequence, stage, app, now, **retry)
    metrics.observe(root, run_id, sequence, duration, usage=usage)
    metrics.reconcile(root, run_id, sequence, disposition, answer_reuse=reuse)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_reserve_observe_and_restart_keep_unmeasured_unknown(self):
        self.assertEqual(metrics.load_events(self.root), [])
        metrics.reserve(self.root, 'run-test', 1, 'submit', 'one', NOW)
        before = metrics.load_events(self.root)[0]
        self.assertEqual(before['reserved_at'], '2026-10-20T09:00:00+08:00')
        self.assertEqual(before['status'], 'reserved')
        self.assertIsNone(before['duration_seconds'])
        metrics.reconcile(self.root, 'run-test', 1, 'verified')
        recovered = metrics.load_events(self.root)[0]
        self.assertIsNone(recovered['duration_seconds'])
        self.assertIsNone(recovered['usage'])
        with self.assertRaises(ValueError):
            metrics.reserve(self.root, 'run-test', 1, 'submit', 'one', NOW)
        self.assertEqual(metrics.summarize([], [recovered])['reserved_applications'], 1)
        self.assertEqual(metrics.summarize([], [recovered])['observed_attempted_applications'], 0)

    def test_transitive_identity_confirmations_and_honest_effort_coverage(self):
        first = job('one', submissions=[confirmation()])
        bridge = job('bridge', listing_url=first['listing_url'], employer_requisition_id='synthetic-req')
        alias = job('alias', employer_requisition_id='synthetic-req')
        for i, (stage, duration) in enumerate((('prepare', 2), ('review', 3), ('submit', 5)), 1):
            measured(self.root, i, stage, app='alias', duration=duration)
        measured(self.root, 4, 'prepare', app='partial', duration=9)
        metrics.reserve(self.root, 'run-test', 5, 'submit', 'partial', NOW)
        records = [first, bridge, alias, job('partial', submissions=[confirmation()]),
                   job('legacy', submissions=[confirmation()]), job('false-claim', status='submitted')]
        report = metrics.summarize(records, metrics.load_events(self.root))
        self.assertEqual(report['unique_confirmed_applications'], 3)
        self.assertEqual(report['reserved_applications'], 2)
        self.assertEqual(report['observed_attempted_applications'], 2)
        self.assertEqual(report['confirmed_with_complete_effort'], 1)
        self.assertEqual(report['confirmed_with_partial_effort'], 1)
        self.assertEqual(report['confirmed_without_effort'], 1)
        self.assertEqual(report['median_effort_seconds'], 10)
        self.assertEqual(report['incomplete_actions'], 1)
        self.assertEqual(report, metrics.summarize(reversed(records), reversed(metrics.load_events(self.root))))

    def test_partial_usage_and_answer_reuse_are_never_zero_filled(self):
        measured(self.root, 1, 'prepare', usage={'tokens': 0, 'cost': 0}, reuse=0)
        measured(self.root, 2, 'review', usage={'tokens': 7})
        metrics.reserve(self.root, 'run-test', 3, 'submit', 'one', NOW)
        report = metrics.summarize([], metrics.load_events(self.root))
        self.assertEqual(report['provider_usage'], {'tokens': None, 'cost': None})
        self.assertEqual(report['provider_coverage'], {'reserved_actions': 3,
            'measured_tokens_actions': 2, 'measured_tokens': 7,
            'measured_cost_actions': 1, 'measured_cost': 0})
        self.assertEqual(report['answer_reuse']['resolved_rows'], 0)
        rows = [dict(resolution='resolved', answer=False, source_answer_id='saved'),
                dict(resolution='resolved', answer=0, source_answer_id='saved'),
                dict(resolution='expired', answer=1, source_answer_id='saved'),
                dict(resolution='resolved', answer=None, source_answer_id='saved'),
                dict(resolution='resolved', answer='manual')]
        self.assertEqual(metrics.known_answer_reuse(rows), 2)
        self.assertIsNone(metrics.summarize([], [])['answer_reuse']['resolved_rows'])
        self.assertEqual(metrics.summarize([], [])['provider_usage'], {'tokens': None, 'cost': None})

    def test_retry_counter_is_context_not_repeated_on_later_stages_or_runs(self):
        measured(self.root, 1, 'prepare', retry_reason='timeout', retry_count=1)
        measured(self.root, 2, 'review', retry_reason='timeout', retry_count=1)
        measured(self.root, 1, 'submit', run_id='run-next', retry_reason='timeout', retry_count=1)
        measured(self.root, 2, 'submit', run_id='run-next', retry_reason='timeout', retry_count=2)
        result = metrics.summarize([], metrics.load_events(self.root))
        self.assertEqual(result['retries'], [{'reason': 'timeout', 'count': 2}])

    def test_shareable_aggregate_has_no_identifiers_or_arbitrary_labels(self):
        marker = 'CANARY-SENSITIVE-company-url-resume-question'
        measured(self.root, 1, 'prepare', app=marker, reuse=1)
        record = job(marker, company=marker, role=marker, listing_url='https://example.invalid/' + marker,
                     submissions=[confirmation(confirmation_path=marker)], preset=marker,
                     blocked_on_answer_ids=[marker], resume_filename=marker)
        rendered = json.dumps(metrics.summarize([record], metrics.load_events(self.root)), allow_nan=False)
        self.assertNotIn(marker, rendered)
        self.assertNotIn('run-test', rendered)
        self.assertNotIn('example.invalid', rendered)
        with self.assertRaises(ValueError):
            metrics.reserve(self.root, 'run-test', 2, marker, marker, NOW)
        with self.assertRaises(ValueError):
            metrics.reserve(self.root, 'run-test', 2, 'prepare', marker, NOW, retry_reason=marker, retry_count=1)

    def test_escaping_corrupt_and_duplicate_ledgers_fail_closed(self):
        measured(self.root, 1, 'prepare')
        events = metrics.load_events(self.root)
        with self.assertRaises(ValueError):
            metrics.summarize([], events + events)
        for value in (-1, True, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metrics.observe(self.root, 'run-test', 1, value)
        path = self.root / 'applications/runs/run-test/metrics.json'
        original = path.read_bytes()
        path.write_text('{"schema_version": 2}')
        with self.assertRaises(ValueError):
            metrics.load_events(self.root)
        path.write_bytes(original)
        with tempfile.TemporaryDirectory() as outside:
            (self.root / 'applications/runs/escape').symlink_to(outside, target_is_directory=True)
            (Path(outside) / 'metrics.json').write_text('{}')
            with self.assertRaises(ValueError):
                metrics.load_events(self.root)


class RunnerMetricsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        w.atomic_json(self.root / 'settings.json', {'timezone': 'Asia/Singapore'})
        settings.run_start(self.root, 'run-test', NOW)
        self.original_run = (self.root / 'applications/runs/run-test/run.json').read_bytes()
        self.parent = Parent(self.root)
        self.parent.put(job('one'))
        self.timer = FakeMonotonic()

    def controller(self, adapter=None, hooks=None):
        return r.Runner(self.root, 'run-test', hooks=hooks or self.parent.hooks(),
                        adapter=adapter or self.parent.adapter, monotonic=self.timer)

    def test_only_adapter_boundaries_are_measured_and_usage_is_observed(self):
        outer = self
        hooks = self.parent.hooks()
        original_hooks = hooks
        def guard(action, now):
            original_hooks.guard(action, now)
            self.timer.advance(100)
        def begin(action, now):
            self.timer.advance(200)
            return original_hooks.begin_submit(action, now)
        def verify(action, observation, now):
            self.timer.advance(300)
            return original_hooks.verify(action, observation, now)
        def adapter(action):
            row = metrics.load_events(outer.root)[-1]
            self.assertEqual(row['status'], 'reserved')
            self.assertIsNone(row['duration_seconds'])
            self.timer.advance({'prepare': 2, 'review': 3, 'submit': 5}[action.kind.value])
            self.parent.adapter(action)
            return r.Observation(usage={'tokens': 7, 'cost': .01})
        hooks = replace(hooks, guard=guard, begin_submit=begin, verify=verify)
        result = self.controller(adapter, hooks).execute(clock=lambda: NOW, max_steps=3)
        self.assertEqual([a.kind for a in result], [r.Kind.PREPARE, r.Kind.REVIEW, r.Kind.SUBMIT])
        events = metrics.load_events(self.root)
        self.assertEqual([e['duration_seconds'] for e in events], [2, 3, 5])
        self.assertTrue(all(e['disposition'] == 'verified' for e in events))
        summary = metrics.summarize(w.records(self.root), events)
        self.assertEqual(summary['median_effort_seconds'], 10)
        self.assertEqual(summary['provider_usage'], {'tokens': 21, 'cost': .03})
        self.assertEqual((self.root / 'applications/runs/run-test/run.json').read_bytes(), self.original_run)

    def test_interrupted_submit_reconciles_without_duration_or_second_call(self):
        self.parent.put(job('one', status='ready', prepared={'review_snapshot': self.parent.ref}))
        calls = []
        def crash(action):
            calls.append(action)
            self.timer.advance(19)
            raise SystemExit('Synthetic interruption')
        with self.assertRaises(SystemExit):
            self.controller(crash).execute(clock=lambda: NOW)
        self.assertIsNone(metrics.load_events(self.root)[0]['duration_seconds'])
        self.timer.advance(9999)
        self.assertEqual(self.controller(crash).execute(clock=lambda: NOW + timedelta(hours=1))[0].reason,
                         'submission_uncertain')
        self.assertEqual(len(calls), 1)
        # Later independent confirmation is recovery evidence, not a new measurement.
        record = self.parent.get('one')
        record.update(submission_intent=None, status='submitted', submissions=[confirmation()])
        self.parent.put(record)
        w.atomic_json(self.root / 'applications/evidence/result-1.json', {'observed': True})
        self.controller().plan_next(NOW + timedelta(hours=2))
        event = metrics.load_events(self.root)[0]
        self.assertEqual(event['disposition'], 'verified')
        self.assertIsNone(event['duration_seconds'])
        self.assertIsNone(event['usage'])
        self.assertEqual(len(calls), 1)

    def test_logging_failure_after_action_never_replays_and_leaves_unknown(self):
        with patch.object(metrics, 'observe', side_effect=OSError('Synthetic disk failure')):
            result = self.controller().execute(clock=lambda: NOW)
        self.assertEqual(result[0].kind, r.Kind.HANDOFF)
        self.assertEqual(result[0].reason, 'metrics_unavailable')
        self.assertFalse(any(call.startswith('failure:') for call in self.parent.calls))
        self.assertEqual(self.parent.calls.count('adapter'), 1)
        self.controller().plan_next(NOW)
        self.assertEqual(self.parent.calls.count('adapter'), 1)
        self.assertIsNone(metrics.load_events(self.root)[0]['duration_seconds'])

    def test_reservation_failure_prevents_adapter_and_plan_alone_is_unmeasured(self):
        self.controller().plan_next(NOW)
        self.assertEqual(metrics.load_events(self.root), [])
        with patch.object(metrics, 'reserve', side_effect=OSError('Synthetic disk failure')):
            result = self.controller().execute(clock=lambda: NOW)
        self.assertEqual(result[0].reason, 'metrics_unavailable')
        self.assertNotIn('adapter', self.parent.calls)
        self.assertEqual(self.controller().execute(clock=lambda: NOW)[0].reason, 'unverified_action')
        self.assertEqual(metrics.load_events(self.root), [])

    def test_typed_retry_is_measured_with_the_actual_provider_delta(self):
        self.parent.put(retry_policy.next_retry_state(job('one'), 'timeout', NOW - timedelta(minutes=2)))
        def adapter(action):
            self.timer.advance(4)
            return r.Observation(failure=retry_policy.Reason.TIMEOUT, usage={'tokens': 3})
        self.parent.disposition = 'unresolved'
        self.controller(adapter).execute(clock=lambda: NOW)
        event = metrics.load_events(self.root)[0]
        self.assertEqual((event['retry_reason'], event['retry_count']), ('timeout', 1))
        self.assertEqual(event['failure_reason'], 'timeout')
        self.assertEqual(event['duration_seconds'], 4)
        self.assertEqual(event['usage'], {'tokens': 3, 'cost': None})

    def test_exception_duration_is_measured_and_error_text_is_discarded(self):
        marker = 'CANARY-PRIVATE-exception-text'
        def adapter(action):
            self.timer.advance(4)
            raise RuntimeError(marker)
        self.controller(adapter).execute(clock=lambda: NOW)
        event = metrics.load_events(self.root)[0]
        self.assertEqual(event['status'], 'failed')
        self.assertEqual(event['duration_seconds'], 4)
        self.assertEqual(event['failure_reason'], 'unsupported')
        self.assertIsNone(event['usage'])
        self.assertNotIn(marker, json.dumps(event))


# Exercise the real workspace answer hook with synthetic immutable snapshots.
class WorkspaceMetricsTests(unittest.TestCase):
    setUp = parent_fixtures.RunnerWorkspaceTests.setUp
    add_release = parent_fixtures.RunnerWorkspaceTests.add_release
    save_registry = parent_fixtures.RunnerWorkspaceTests.save_registry
    job = parent_fixtures.RunnerWorkspaceTests.job
    record = parent_fixtures.RunnerWorkspaceTests.record
    initialize = parent_fixtures.RunnerWorkspaceTests.initialize

    def test_real_answer_snapshot_counts_resolved_saved_rows_only(self):
        self.initialize()
        # A blocked new preparation must not reuse the previous ready snapshot's
        # row count just because its `prepared` reference still exists.
        old_path = f'applications/records/{self.app}/old-answers.json'
        w.atomic_json(self.root / old_path, [
            {'resolution': 'resolved', 'answer': 'Yes', 'source_answer_id': 'old-a'},
            {'resolution': 'resolved', 'answer': 'Yes', 'source_answer_id': 'old-b'}])
        saved = self.record(self.app)
        saved['prepared'] = {'answers_path': old_path}
        w.atomic_json(self.root / f'applications/records/{self.app}/record.json', saved)
        w.atomic_json(self.root / 'profile/answers.json', {'answers': [{
            'id': 'synthetic-saved', 'value': 'Yes', 'type': 'text', 'scope': {},
            'aliases': ['Synthetic known?'], 'source': 'Synthetic confirmation',
            'confirmed_at': (self.now - timedelta(days=1)).isoformat()}]})
        timer = FakeMonotonic()
        before = {}
        def adapter(action):
            prepared = w.prepare_answers(self.root, action.application_id,
                [{'question': 'Synthetic known?'}, {'question': 'Synthetic unknown?'}], self.now, deferred=True)
            before['path'] = self.root / prepared['answers_path']
            before['bytes'] = before['path'].read_bytes()
            timer.advance(8)
            return r.Observation()
        result = build_runner(self.root, 'fixture-run', self.brief, adapter, monotonic=timer).execute(clock=lambda: self.now)
        self.assertEqual(result[0].kind, r.Kind.PREPARE)
        row = metrics.load_events(self.root)[0]
        self.assertEqual(row['answer_reuse'], 1)
        self.assertEqual(row['duration_seconds'], 8)
        self.assertEqual(row['disposition'], 'verified')
        self.assertEqual(before['path'].read_bytes(), before['bytes'])
        self.assertEqual(self.record(self.app)['status'], 'awaiting_answer')


if __name__ == '__main__':
    unittest.main()
