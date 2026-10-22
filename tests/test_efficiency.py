"""Synthetic issue #55 fixtures. No authenticated browser or personal workspace."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discovery
import resume_assets
import spans
import workspace as w
import settings
from test_runner import NOW, job, confirmation
import test_workflow as fixtures


class Clock:
    def __init__(self): self.seconds = 0
    def mono(self): return self.seconds + 10000
    def wall(self): return NOW + timedelta(seconds=self.seconds)
    def advance(self, seconds): self.seconds += seconds


class ResumeAssets(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    add_release = fixtures.WorkflowTests.add_release
    save_registry = fixtures.WorkflowTests.save_registry

    def bind(self):
        self.asset = dict(account_id='synthetic-account', asset_id='opaque-asset', revision='1')
        self.content = (self.root/self.application['path']).read_bytes()
        self.current = resume_assets.remember(self.root, 'backend', self.asset, self.content, self.now, method='verified_upload')

    def test_exact_receipt_only_and_independent_byte_verification(self):
        self.bind()
        self.assertTrue(resume_assets.verify(self.root, 'backend', self.asset, self.current, self.now))
        for key in self.asset:
            changed = {**self.asset, key: 'changed'}
            self.assertFalse(resume_assets.verify(self.root, 'backend', changed, self.current, self.now))
        self.assertFalse(resume_assets.verify(self.root, 'backend', self.asset, self.current, self.now+timedelta(days=1)))
        with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
            resume_assets.remember(self.root, 'backend', self.asset, b'different', self.now, method='verified_upload')
        self.assertTrue(resume_assets.verify(self.root, 'backend', self.asset, self.current, self.now))

    def test_release_changes_withdrawal_supersession_expiry_and_review_due_block(self):
        self.bind(); original = deepcopy(self.application)
        for change in [{'status':'withdrawn'}, {'valid_until':self.now.isoformat()}, {'review_due_at':self.now.isoformat()}, {'sha256':'0'*64}]:
            self.application.update(change); self.save_registry()
            with self.assertRaises(ValueError): resume_assets.verify(self.root, 'backend', self.asset, self.current, self.now)
            self.application.clear(); self.application.update(original)
        replacement = self.add_release('backend-v2', 'backend', 'application', master='master-v1')
        replacement['effective_from'] = self.now.isoformat(); replacement['released_at'] = self.now.isoformat()
        self.registry['releases'].append(replacement); self.save_registry()
        with self.assertRaisesRegex(ValueError, 'resume_changed'):
            resume_assets.verify(self.root, 'backend', self.asset, self.current, self.now)
        new = w.resolve(self.registry, 'backend', 'application', self.now, self.root)
        self.assertFalse(resume_assets.verify(self.root, 'backend', self.asset, new, self.now))


class SpanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.clock = Clock(); self.rec = spans.Recorder(self.root, 'run-test', monotonic=self.clock.mono, wall=self.clock.wall)

    def test_exact_segments_nested_waits_and_quantiles(self):
        with self.rec.span('workflow', 'orchestration'):
            for seconds in [1,2,3,4,5]:
                with self.rec.span('navigation', 'browser'): self.clock.advance(seconds)
            with self.rec.span('handoff', 'user_wait'): self.clock.advance(5)
        rows = spans.load(self.root); report = spans.summarize([], rows)
        self.assertEqual(report['summed_process_effort_seconds'], 20)
        self.assertEqual(report['critical_path_active_seconds'], 15)
        phase = next(r for r in report['phases'] if r['phase']=='navigation')
        self.assertEqual(phase, {'phase':'navigation','samples':5,'median_seconds':3,'p95_seconds':5})
        self.assertEqual(rows[0]['duration_seconds'],20); self.assertIn('+08:00',rows[0]['started_at'])
        self.assertEqual(report, spans.summarize([], reversed(rows)))
        self.assertEqual(report['unique_confirmed_applications'],0)
        self.assertEqual(report['confirmations_per_measured_active_hour'],0)

    def test_restart_open_span_and_metrics_failure_do_not_replay(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.rec.span('workflow'): self.clock.advance(10); raise KeyboardInterrupt()
        before = spans.load(self.root)[0]; self.assertIsNone(before['duration_seconds'])
        restarted = spans.Recorder(self.root,'run-test',monotonic=lambda: 7,wall=self.clock.wall)
        with restarted.span('review'): pass
        rows = spans.load(self.root)
        self.assertNotEqual(rows[0]['process_id'],rows[1]['process_id'])
        self.assertIsNone(rows[0]['duration_seconds'])
        report = spans.summarize([],rows)
        self.assertEqual(report['coverage'],'partial'); self.assertIsNone(report['wall_run_seconds'])
        calls=[]
        with patch.object(restarted,'_save',side_effect=OSError('synthetic')):
            self.assertEqual(restarted.call('submission','browser',lambda: calls.append(1) or 42),42)
        self.assertEqual(calls,[1]); self.assertFalse(restarted.available)

    def test_concurrent_processes_and_nested_imports_never_add_to_active_denominator(self):
        with self.rec.span('workflow') as parent:
            self.clock.advance(10)
        row = deepcopy(parent)
        row.update(span_id='external',process_id='other-process',parent_id=parent['span_id'], phase='navigation', channel='browser')
        opened={**row,'outcome':'open','coverage':'open','duration_seconds':None,'ended_at':None}
        self.assertTrue(self.rec.ingest(opened));self.assertTrue(self.rec.ingest(row))
        report = spans.summarize([],spans.load(self.root))
        self.assertEqual(report['critical_path_active_seconds'],10)
        self.assertEqual(report['summed_process_effort_seconds'],20)
        # Concurrent siblings within one process also overlap, not sum.
        second = {**row,'span_id':'sibling'}
        self.assertTrue(self.rec.ingest({**opened,'span_id':'sibling'}));self.assertTrue(self.rec.ingest(second))
        self.assertEqual(spans.summarize([],spans.load(self.root))['summed_process_effort_seconds'],20)

    def test_live_import_reservation_completion_and_unknown_history(self):
        with self.rec.span('workflow') as parent: self.clock.advance(1)
        row = {**parent,'span_id':'browser-span','parent_id':parent['span_id'],'process_id':'browser','phase':'form_observation','channel':'browser',
            'duration_seconds':None,'ended_at':None,'coverage':'open','outcome':'open'}
        self.assertTrue(self.rec.ingest(row)); self.assertTrue(self.rec.ingest({**row,'duration_seconds':1,'ended_at':parent['ended_at'],'coverage':'measured','outcome':'ok'}))
        self.assertEqual(len(spans.load(self.root)),2)
        legacy=spans.summarize([job('legacy',submissions=[confirmation()])],[])
        self.assertEqual(legacy['unique_confirmed_applications'],1)
        for key in ['critical_path_active_seconds','wall_run_seconds','confirmations_per_measured_active_hour']:
            self.assertIsNone(legacy[key])
        self.assertIsNone(legacy['counters']['uploads'])

    def test_duplicate_crosspost_retry_partial_confirmation_first_time_and_privacy(self):
        with self.rec.span('workflow'):
            with self.rec.span('retry',counters={'retries':1}): self.clock.advance(2)
        records=[job('first',submissions=[confirmation(submitted_at=(NOW+timedelta(seconds=1)).isoformat())]),
            job('bridge',listing_url=job('first')['listing_url'],employer_requisition_id='synthetic-req'),
            job('alias',employer_requisition_id='synthetic-req',submissions=[confirmation(submitted_at=(NOW+timedelta(seconds=2)).isoformat())]),
            job('false-claim',status='submitted')]
        report=spans.summarize(records,spans.load(self.root))
        self.assertEqual(report['unique_confirmed_applications'],1);self.assertEqual(report['time_to_first_confirmation_seconds'],1)
        self.assertEqual(report['retry_seconds'],2);self.assertEqual(report['counters']['retries'],1)
        self.assertNotIn('synthetic-req',json.dumps(report));self.assertNotIn('run-test',json.dumps(report))
        self.assertEqual(spans.summarize(records,spans.load(self.root),start=NOW+timedelta(seconds=2))['unique_confirmed_applications'],0)


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_locked_snapshot_reads_once_and_mutations_invalidate_next_read(self):
        w.atomic_json(self.root/'applications/records/one/record.json',job('one'))
        original=w.read
        with w.writer(self.root), w.planning_snapshot(self.root), patch.object(w,'read',wraps=original) as reads:
            first=w.records(self.root); first[0]['status']='modified-copy'
            self.assertEqual(w.records(self.root)[0]['status'],'discovered');self.assertEqual(reads.call_count,1)
            w.atomic_json(self.root/'applications/records/one/record.json',job('one',submission_intent={}))
            self.assertIn('submission_intent',w.records(self.root)[0]);self.assertEqual(reads.call_count,2)
        with patch.object(w,'read',wraps=original) as reads:
            w.records(self.root);w.records(self.root);self.assertEqual(reads.call_count,2)

    def test_saved_priority_transitive_uncertainty_duplicates_and_ambiguous_headers(self):
        base=job('known',status='submission_uncertain')
        bridge=job('bridge',listing_url=base['listing_url'],employer_requisition_id='synthetic-req')
        cards=[dict(job('unused'), source='search'), dict(job('unknown'),source='saved'),
            dict(job('alias',employer_requisition_id='synthetic-req'),source='saved'),dict(job('unknown'),source='saved')]
        report=discovery.screen_cards([base,bridge],cards,[settings.validate_settings({})])
        self.assertEqual([r['index'] for r in report['cards']],[1,2,3,0])
        self.assertEqual([r['decision'] for r in report['cards']],['inspect_listing','excluded_identity','duplicate_card','inspect_listing'])

    def test_confirmation_between_screen_and_navigation_prevents_open_and_stop_prevents_tools(self):
        cards=[{**job('one'),'source':'saved'}]; state=[]; opens=[]
        class Adapter:
            def saved_cards(self,limit):return cards
            def open_listing(self,card):opens.append(1);return card
            def capture(self,listing):return listing
        checks=[]
        def guard():
            checks.append(1)
            if len(checks)==2:state.append(job('confirmed',listing_url=cards[0]['listing_url'],submission_intent={}))
            return True
        self.assertEqual(discovery.discover_saved(Adapter(),read_records=lambda:state,policies=lambda:[settings.validate_settings({})],guard=guard),[])
        self.assertEqual(opens,[])
        with self.assertRaisesRegex(ValueError,'guard_refused'):
            discovery.discover_saved(Adapter(),read_records=lambda:state,policies=lambda:[],guard=lambda:False)

    def test_synthetic_baseline_reproduces_checked_in_aggregate(self):
        import synthetic_baseline
        expected=json.loads((Path(__file__).resolve().parents[1]/'docs/synthetic-baseline.json').read_text())
        self.assertEqual(synthetic_baseline.baseline(),expected)

    def test_header_exclusions_are_cheap_and_changed_policy_applies_before_open(self):
        cards=[{**job('one'),'company':'Blocked Example','source':'saved'}]
        policies=[settings.validate_settings({})]; opens=[]; checks=[]
        class Adapter:
            def saved_cards(self,limit): return cards
            def open_listing(self,card): opens.append(1); return card
            def capture(self,listing): return listing
        def guard():
            checks.append(1)
            if len(checks)==2: policies[0]=settings.validate_settings({'company_blocklist':['Blocked']})
            return True
        self.assertEqual(discovery.discover_saved(Adapter(),read_records=lambda:[],policies=lambda:policies,guard=guard),[])
        self.assertEqual(opens,[])

class ModelMeasurementTests(unittest.TestCase):
    setUp = SpanTests.setUp
    def test_actual_output_tokens_have_matching_generation_interval(self):
        model={'provider':'synthetic-provider','model':'fixture-model','configuration':'default'}
        def generate(): self.clock.advance(2); return 'synthetic-value',10
        self.assertEqual(self.rec.generation('answer_resolution',model,generate),'synthetic-value')
        self.rec.generation('review',model,lambda:('unknown-usage',None))
        report=spans.summarize([],spans.load(self.root))
        self.assertEqual(report['output_tokens_per_second'],5)
        self.assertEqual(report['measured_generations'],1)
        self.assertEqual(report['model_measurements'],2)
        self.assertNotIn('fixture-model',json.dumps(report))

class RunnerIntegrationTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    evidence=fixtures.WorkflowTests.evidence

    def initialize(self):
        from test_runner_workspace import RunnerWorkspaceTests
        RunnerWorkspaceTests.initialize(self)

    def test_queue_reuses_one_snapshot_and_next_action_sees_changed_stop_or_policy(self):
        from runner_workspace import build_runner
        self.initialize(); controller=build_runner(self.root,'fixture-run',self.brief)
        original=w.read
        with w.writer(self.root), patch.object(w,'read',wraps=original) as read:
            controller.hooks.queue('fixture-run',self.now)
            record_reads=[c for c in read.call_args_list if c.args[0].name=='record.json']
            self.assertEqual(len(record_reads),1)
        controller.plan_next(self.now)
        w.safety.stop_run(self.root,'fixture-run','manual_stop',self.now)
        calls=[];controller.adapter=lambda a:calls.append(a)
        self.assertEqual(controller.execute(clock=lambda:self.now)[0].reason,'gate_blocked')
        self.assertEqual(calls,[])

    def test_live_perform_boundary_measures_actual_tools_without_changing_observation(self):
        from runner import Observation
        from runner_workspace import build_runner
        self.initialize()
        class Adapter:
            def perform(self,action,boundary):
                return boundary.call('navigation','browser',lambda:Observation())
        controller=build_runner(self.root,'fixture-run',self.brief,Adapter())
        controller.execute(clock=lambda:self.now)
        rows=spans.load(self.root)
        navigation=next(r for r in rows if r['phase']=='navigation')
        self.assertEqual(navigation['application_id'],self.app);self.assertEqual(navigation['action_id'],'1')
        self.assertIsNotNone(navigation['parent_id']);self.assertEqual(navigation['coverage'],'measured')
        # No authoritative preparation occurred: telemetry cannot verify success.
        self.assertIsNotNone(w.read(self.root/'applications/runs/fixture-run/state.json')['pending'])

    def test_private_guarded_resume_boundary_requires_platform_bytes(self):
        self.initialize()
        asset={'account_id':'synthetic','asset_id':'opaque','revision':'1'}
        allowed=[True]
        boundary=resume_assets.Boundary(self.root,'backend',guard=lambda:allowed[0],clock=lambda:self.now,
            read_asset_bytes=lambda observed:(self.root/self.application['path']).read_bytes())
        current=boundary.resolve();request={'asset':asset,'resume':current}
        self.assertFalse(boundary.verify(request));self.assertTrue(boundary.remember(request));self.assertTrue(boundary.verify(request))
        allowed[0]=False
        with self.assertRaisesRegex(ValueError,'guard_refused'):boundary.verify(request)

class ConcurrentWaitTests(unittest.TestCase):
    setUp=SpanTests.setUp

    def test_one_process_wait_does_not_erase_another_process_active_work(self):
        with self.rec.span('workflow') as outer:
            with self.rec.span('handoff','user_wait'): self.clock.advance(10)
        row={**outer,'span_id':'parallel','parent_id':outer['span_id'],'process_id':'parallel-process','phase':'navigation','channel':'browser'}
        opened={**row,'ended_at':None,'duration_seconds':None,'outcome':'open','coverage':'open'}
        self.assertTrue(self.rec.ingest(opened));self.assertTrue(self.rec.ingest(row))
        summary=spans.summarize([],spans.load(self.root))
        self.assertEqual(summary['critical_path_active_seconds'],10)
        self.assertEqual(len(summary['processes']),2)

class CrossLanguageContractTests(unittest.TestCase):
    setUp=SpanTests.setUp

    def test_actual_js_model_boundary_is_accepted_by_python_private_ledger(self):
        import subprocess
        source="""
const T=require(process.argv[1]);let seconds=0;const rows=[];
const t=T.create({context:{run_id:'run-test',application_id:'synthetic-app',attempt_id:'attempt',action_id:'1'},processId:'synthetic-js-process',monotonic:()=>seconds,wall:()=>new Date(Date.parse('2026-10-20T01:00:00Z')+seconds*1000),emit:r=>{rows.push(r);seconds+=10;}});
(async()=>{await t.call('answer_resolution','model',()=>{seconds+=2;return {output_tokens:12};},{model:{provider:'synthetic',model:'fixture',configuration:'default'},outputUsage:r=>{seconds+=3;return r.output_tokens;}});console.log(JSON.stringify(rows));})();
"""
        script=Path(__file__).resolve().parents[1]/'scripts/telemetry.js'
        rows=json.loads(subprocess.run(['node','-e',source,str(script)],capture_output=True,text=True,check=True,timeout=5).stdout)
        for row in rows:self.assertTrue(self.rec.ingest(row))
        report=spans.summarize([],spans.load(self.root))
        self.assertEqual(report['output_tokens_per_second'],6)
        self.assertEqual(rows[1]['duration_seconds'],15)
        self.assertEqual(rows[1]['model']['generation_seconds'],2)
        self.assertEqual(report['measured_spans'],1)
        self.assertEqual(spans.quantiles([1,2,3,4]),{'samples':4,'median_seconds':2.5,'p95_seconds':4})

class DescendantWaitRegressionTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def rows(self):
        with self.rec.span('workflow', 'orchestration') as parent:
            self.clock.advance(10)
        child = {**parent, 'span_id':'child', 'parent_id':parent['span_id'],
                 'process_id':'browser', 'phase':'form_filling', 'channel':'browser'}
        wait = {**child, 'span_id':'wait', 'parent_id':'child',
                'phase':'handoff', 'channel':'user_wait'}
        return [parent, child, wait]

    def test_cross_process_fully_blocking_descendant_wait_erases_inclusive_ancestors(self):
        for channel in ('user_wait', 'blocked_wait'):
            rows = self.rows()
            rows[-1]['channel'] = channel
            result = spans.summarize([], rows)
            self.assertEqual(result['critical_path_active_seconds'], 0)
            self.assertEqual(result['summed_process_effort_seconds'], 20)

    def test_overlapping_waits_and_independent_sibling_preserve_only_real_concurrency(self):
        rows = self.rows()
        rows[-1].update(started_at=(NOW+timedelta(seconds=2)).isoformat(),
                        ended_at=(NOW+timedelta(seconds=8)).isoformat(), duration_seconds=6)
        rows.append({**rows[-1], 'span_id':'wait2',
                     'started_at':(NOW+timedelta(seconds=5)).isoformat(),
                     'ended_at':(NOW+timedelta(seconds=9)).isoformat(), 'duration_seconds':4})
        self.assertEqual(spans.summarize([], rows)['critical_path_active_seconds'], 3)
        rows.append({**rows[1], 'span_id':'independent', 'process_id':'parallel',
                     'started_at':(NOW+timedelta(seconds=4)).isoformat(),
                     'ended_at':(NOW+timedelta(seconds=7)).isoformat(), 'duration_seconds':3})
        self.assertEqual(spans.summarize([], rows)['critical_path_active_seconds'], 6)
        # Explicit concurrent siblings remain work even in one async process.
        rows[-1]['process_id'] = 'browser'
        self.assertEqual(spans.summarize([], rows)['critical_path_active_seconds'], 6)

    def test_period_keeps_dependencies_of_inclusive_parent_samples(self):
        rows = self.rows()
        rows[-1].update(started_at=(NOW+timedelta(seconds=2)).isoformat(),
                        ended_at=(NOW+timedelta(seconds=8)).isoformat(), duration_seconds=6)
        period = {'start':NOW, 'end':NOW+timedelta(seconds=1)}
        self.assertEqual(spans.summarize([], rows, **period)['critical_path_active_seconds'], 4)
        rows[-1].update(ended_at=None, duration_seconds=None, coverage='open', outcome='open')
        self.assertIsNone(spans.summarize([], rows, **period)['critical_path_active_seconds'])

    def test_unknown_descendant_wait_remains_unknown(self):
        rows = self.rows()
        rows[-1].update(ended_at=None, duration_seconds=None, coverage='open', outcome='open')
        self.assertIsNone(spans.summarize([], rows)['critical_path_active_seconds'])

class ConnectedBaselineRegressionTests(unittest.TestCase):
    def test_connected_executor_uses_actual_receipts_ingestion_and_confirmations(self):
        import synthetic_baseline
        verify, ingest = resume_assets.verify, spans.Recorder.ingest
        imported = []
        def ingest_row(recorder, row):
            imported.append(deepcopy(row))
            return ingest(recorder, row)
        with patch.object(resume_assets, 'verify', wraps=verify) as verification, patch.object(spans.Recorder, 'ingest', ingest_row):
            report = synthetic_baseline.connected_run()
        self.assertGreater(verification.call_count, 0)
        self.assertEqual(report['uploads'], 0)
        self.assertEqual(report['clicks'], 1)
        self.assertEqual(report['unique_confirmations'], 1)
        self.assertTrue(report['workflow_completed'])
        self.assertTrue(report['export_clean'])
        self.assertEqual(len(imported), report['imported_form_spans']*2)
        self.assertTrue(all(row['parent_id'] and row['application_id'] and row['attempt_id'] for row in imported))
        self.assertEqual(report['wall_run_seconds'], report['measured_active_seconds'])
        self.assertGreater(report['wall_run_seconds'], report['time_to_first_confirmation_seconds'])

    def test_connected_corrupt_receipt_forces_real_verified_upload(self):
        import synthetic_baseline
        report = synthetic_baseline.connected_run(fault='receipt_corrupt')
        self.assertEqual(report['uploads'], 1)
        self.assertEqual(report['path_entries'], 1)
        self.assertEqual(report['unique_confirmations'], 1)

    def test_both_schedules_refuse_failed_readback_or_independent_review(self):
        import synthetic_baseline
        original = w.event
        for reference in (False, True):
            for fault in ('write_ignored', 'review_mismatch'):
                with self.subTest(reference=reference, fault=fault):
                    statuses = []
                    def event(root, args, *a, **kw):
                        statuses.append(args.status)
                        return original(root, args, *a, **kw)
                    with patch.object(w, 'event', side_effect=event):
                        with self.assertRaisesRegex(ValueError, 'synthetic_workflow_not_verified'):
                            synthetic_baseline.connected_run(reference=reference, fault=fault)
                    self.assertNotIn('submitted', statuses)

class OrphanedWaitRegressionTests(unittest.TestCase):
    setUp = SpanTests.setUp
    rows = DescendantWaitRegressionTests.rows
    def test_orphaned_wait_dependency_is_not_a_confirmed_active_denominator(self):
        rows = self.rows()
        rows[-1]['parent_id'] = 'unobserved-parent'
        self.assertIsNone(spans.summarize([], rows)['critical_path_active_seconds'])

class NativeReservationRegressionTests(unittest.TestCase):
    setUp = RunnerIntegrationTests.setUp
    add_release = RunnerIntegrationTests.add_release
    save_registry = RunnerIntegrationTests.save_registry
    job = RunnerIntegrationTests.job
    record = RunnerIntegrationTests.record
    evidence = RunnerIntegrationTests.evidence
    initialize = RunnerIntegrationTests.initialize

    def ready(self):
        import argparse
        result = w.prepare_answers(self.root, self.app, [], self.now)
        w.event(self.root, argparse.Namespace(id=self.app, status='ready', note='', resume_scope='backend',
            answers=self.root/result['answers_path'], evidence=None), self.now)
        prepared = self.record(self.app)['prepared']
        w.review_form(self.root, self.app, {'attempt_id':prepared['attempt_id'], 'resume_sha256':prepared['sha256'], 'fields':[]}, self.now)

    def stop_on_reservation(self, target):
        save = spans.Recorder._save
        self.stopped = False
        def persist(recorder):
            save(recorder)
            row = recorder.rows[-1]
            if not self.stopped and row['outcome'] == 'open' and target(row):
                self.stopped = True
                w.safety.stop_run(self.root, 'fixture-run', 'manual_stop', self.now)
        return patch.object(spans.Recorder, '_save', persist)

    def test_stop_during_outer_reservation_blocks_callable_perform_and_url(self):
        from runner import Observation, Kind
        from runner_workspace import build_runner
        for variant in ('callable', 'perform', 'url', 'submit'):
            with self.subTest(variant=variant):
                # Each subcase has an independent synthetic workspace/run.
                self.setUp(); self.initialize()
                if variant in ('url', 'submit'): self.ready()
                calls = []
                outer = self
                class Adapter:
                    def observe_url(self, action):
                        calls.append('url'); return outer.record(action.application_id)['listing_url']
                    def __call__(self, action): calls.append('click'); return Observation()
                if variant == 'perform':
                    Adapter.perform = lambda _self, action, boundary: calls.append('perform') or Observation()
                adapter = Adapter()
                controller = build_runner(self.root, 'fixture-run', self.brief, adapter)
                phase = {'callable':'form_filling', 'perform':'form_filling', 'url':'preflight', 'submit':'submission'}[variant]
                with self.stop_on_reservation(lambda r: r['phase']==phase and r['channel']=='browser'):
                    result = controller.execute(clock=lambda:self.now)
                self.assertTrue(self.stopped)
                self.assertEqual(calls, ['url'] if variant=='submit' else [])
                self.assertEqual(result[0].kind, Kind.HANDOFF)
                self.assertIsNotNone(w.read(self.root/'applications/runs/fixture-run/state.json')['pending'])
                if variant == 'submit':
                    saved = self.record(self.app)
                    self.assertEqual(saved['status'], 'submission_uncertain')
                    self.assertEqual(saved['submission_intent'], w.read(self.root/'applications/browser-claim.json'))
                    build_runner(self.root, 'fixture-run', self.brief, adapter).execute(clock=lambda:self.now)
                    self.assertEqual(calls, ['url'], 'uncertain intent must never replay a click')

    def test_stop_during_each_nested_native_reservation_blocks_callback(self):
        from runner import Observation
        from runner_workspace import build_runner
        for phase in ('navigation', 'form_observation', 'form_readback', 'resume_resolution', 'resume_upload', 'file_picker', 'confirmation'):
            with self.subTest(phase=phase):
                self.setUp(); self.initialize(); calls=[]
                class Adapter:
                    def perform(self, action, boundary):
                        return boundary.call(phase, 'browser', lambda: calls.append(phase) or Observation())
                controller = build_runner(self.root, 'fixture-run', self.brief, Adapter())
                with self.stop_on_reservation(lambda r: r['phase']==phase and r['channel']=='browser'):
                    controller.execute(clock=lambda:self.now)
                self.assertTrue(self.stopped); self.assertEqual(calls, [])
                failed = [r for r in spans.load(self.root) if r['phase']==phase and r['outcome']=='failed']
                self.assertTrue(failed)

    def test_post_claim_guard_keeps_freshness_policy_and_writer_checks(self):
        from runner import Observation
        from runner_workspace import build_runner
        for change in ('policy', 'resume', 'answers', 'review', 'writer', 'claim', 'alias'):
            with self.subTest(change=change):
                self.setUp(); self.initialize(); self.ready(); clicks=[]
                outer=self
                class Adapter:
                    def observe_url(self, action): return outer.record(action.application_id)['listing_url']
                    def __call__(self, action): clicks.append(1); return Observation()
                controller=build_runner(self.root,'fixture-run',self.brief,Adapter())
                save=spans.Recorder._save
                changed=[]
                def persist(recorder):
                    save(recorder)
                    row=recorder.rows[-1]
                    if changed or row['outcome']!='open' or row['phase']!='submission' or row['channel']!='browser': return
                    changed.append(1)
                    prepared=self.record(self.app)['prepared']
                    if change=='policy': w.atomic_json(self.root/'settings.json', {'resume_scope':'backend','mode':'dry_run'})
                    elif change=='resume': self.application['status']='withdrawn'; self.save_registry()
                    elif change=='answers': (self.root/prepared['answers_path']).write_text('[]\nchanged')
                    elif change=='review': (self.root/prepared['review_snapshot']).write_text('{}')
                    elif change=='writer': (self.root/'applications/computer-use.lock').write_text('synthetic')
                    elif change=='alias':
                        alias={**self.record(self.app),'application_id':'synthetic-alias','submission_intent':{}}
                        w.atomic_json(self.root/'applications/records/synthetic-alias/record.json',alias)
                    else: w.atomic_json(self.root/'applications/browser-claim.json', {})
                with patch.object(spans.Recorder,'_save',persist): controller.execute(clock=lambda:self.now)
                self.assertEqual(changed,[1]); self.assertEqual(clicks,[])
                self.assertEqual(self.record(self.app)['status'],'submission_uncertain')
                self.assertTrue(self.record(self.app)['submission_intent'])

class DiscoveryReservationRegressionTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def test_stop_during_saved_navigation_or_capture_reservation_blocks_native_callback(self):
        for phase, expected in [('saved_discovery',[]), ('navigation',['saved']), ('listing_capture',['saved','open'])]:
            with self.subTest(phase=phase):
                self.setUp(); calls=[]; stopped=[]
                class Adapter:
                    def saved_cards(self, limit): calls.append('saved'); return [{**job('one'), 'source':'saved'}]
                    def open_listing(self, card): calls.append('open'); return card
                    def capture(self, listing): calls.append('capture'); return listing
                save=self.rec._save
                def persist():
                    save()
                    if self.rec.rows[-1]['phase']==phase and self.rec.rows[-1]['outcome']=='open': stopped.append(1)
                with patch.object(self.rec,'_save',persist), self.rec.span('workflow'):
                    with self.assertRaisesRegex(ValueError,'discovery_guard_refused'):
                        discovery.discover_saved(Adapter(), read_records=lambda:[], policies=lambda:[settings.validate_settings({})], guard=lambda:not stopped)
                self.assertTrue(stopped); self.assertEqual(calls, expected)

    def test_new_confirmation_during_navigation_reservation_prevents_open(self):
        records=[]; opens=[]
        cards=[{**job('one'),'source':'saved'}]
        class Adapter:
            def saved_cards(self, limit): return cards
            def open_listing(self, card): opens.append(1)
        save=self.rec._save
        def persist():
            save()
            if self.rec.rows[-1]['phase']=='navigation' and self.rec.rows[-1]['outcome']=='open':
                records.append(job('alias',listing_url=cards[0]['listing_url'],submission_intent={}))
        with patch.object(self.rec,'_save',persist), self.rec.span('workflow'):
            self.assertEqual(discovery.discover_saved(Adapter(),read_records=lambda:records,policies=lambda:[settings.validate_settings({})],guard=lambda:True),[])
        self.assertEqual(opens,[])

class GenerationLatencyRegressionTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def test_generation_excludes_reservation_completion_and_usage_extraction_latency(self):
        save=self.rec._save
        def persist(): self.clock.advance(10); save()
        def provider(): self.clock.advance(2); return {'output_tokens':10, 'value':42}
        def usage(value): self.clock.advance(3); return value['output_tokens']
        model={'provider':'synthetic','model':'fixture','configuration':'default'}
        with patch.object(self.rec,'_save',persist):
            result=self.rec.generation('answer_resolution',model,provider,output_usage=usage)
        self.assertEqual(result['value'],42)
        row=spans.load(self.root)[0]
        self.assertEqual(row['duration_seconds'],15)
        self.assertEqual(row['model']['generation_seconds'],2)
        self.assertEqual(self.clock.seconds,25)
        self.assertEqual(spans.summarize([], [row])['output_tokens_per_second'],5)
        invalid=deepcopy(row);invalid['model']['generation_seconds']=16
        with self.assertRaisesRegex(ValueError,'interval_mismatch'): spans.validate(invalid)

    def test_default_tuple_unpacking_is_outside_provider_interval(self):
        outer=self
        class Usage:
            def __iter__(self): outer.clock.advance(3); return iter(('synthetic',10))
        def provider(): self.clock.advance(2); return Usage()
        model={'provider':'synthetic','model':'fixture','configuration':'default'}
        self.assertEqual(self.rec.generation('review',model,provider),'synthetic')
        row=spans.load(self.root)[0]
        self.assertEqual(row['duration_seconds'],5)
        self.assertEqual(row['model']['generation_seconds'],2)

    def test_usage_extraction_failure_cannot_change_or_replay_provider_result(self):
        calls=[]
        def provider(): calls.append(1); self.clock.advance(2); return 42
        def usage(_): self.clock.advance(3); raise ValueError('synthetic')
        model={'provider':'synthetic','model':'fixture','configuration':'default'}
        self.assertEqual(self.rec.generation('review',model,provider,output_usage=usage),42)
        self.assertEqual(calls,[1])
        row=spans.load(self.root)[0]
        self.assertIsNone(row['model']['generation_seconds'])
        self.assertIsNone(spans.summarize([], [row])['output_tokens_per_second'])

class CounterContractRegressionTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def reservation(self):
        with self.rec.span('form_filling','browser',counters={'field_writes':0}) as row:
            self.clock.advance(1)
        complete={**row,'span_id':'native-js','process_id':'js','counters':{'field_writes':1}}
        opened={**complete,'ended_at':None,'duration_seconds':None,'coverage':'open','outcome':'open','counters':{'field_writes':0}}
        return opened, complete

    def test_ingestion_counts_actual_failed_attempt_and_zero_refusal(self):
        for invoked in (False,True):
            with self.subTest(invoked=invoked):
                self.setUp(); opened,complete=self.reservation()
                complete.update(outcome='failed',counters={'field_writes':int(invoked)})
                self.assertTrue(self.rec.ingest(opened));self.assertTrue(self.rec.ingest(complete))
                report=spans.summarize([],spans.load(self.root))
                self.assertEqual(report['counters']['field_writes'],int(invoked))

    def test_open_zero_counter_is_unknown_even_when_callback_might_have_run(self):
        opened,_=self.reservation()
        self.assertTrue(self.rec.ingest(opened))
        report=spans.summarize([],spans.load(self.root))
        coverage=next(r for r in report['counter_coverage'] if r['counter']=='field_writes')
        self.assertEqual(coverage['unknown_spans'],1)
        self.assertEqual(coverage['measured_spans'],1)

    def test_ingestion_refuses_changed_counter_keys_and_decreasing_counts(self):
        for invalid in ('keys','decrease'):
            with self.subTest(invalid=invalid):
                self.setUp();opened,complete=self.reservation()
                if invalid=='keys': complete['counters']={'uploads':1}
                else: opened['counters']={'field_writes':1};complete['counters']={'field_writes':0}
                self.assertTrue(self.rec.ingest(opened))
                self.assertFalse(self.rec.ingest(complete))

class NativeGuardAccountingTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def test_guard_has_no_trailing_span_persistence_but_retains_effort_and_read_counts(self):
        events=[]
        save=self.rec._save
        def persist(): events.append('save'); save()
        @spans.measured('resume_resolution')
        def guard():
            events.append('guard'); spans.count('record_reads'); self.clock.advance(2)
        with patch.object(self.rec,'_save',persist), self.rec.span('workflow'):
            boundary=spans.NativeBoundary(self.rec,guard)
            boundary.call('navigation','browser',lambda:events.append('native'))
        self.assertEqual(events,['save','save','guard','native','save','save'])
        rows=spans.load(self.root)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['duration_seconds'],2)
        self.assertEqual(spans.summarize([],rows)['counters']['record_reads'],1)

class NativeRefusalContractTests(unittest.TestCase):
    setUp = SpanTests.setUp

    def test_native_guards_must_explicitly_succeed_and_refusal_has_zero_attempts(self):
        from runner import Action, Kind
        for variant in ('outer','url','nested'):
            for result in (False,True):
                with self.subTest(variant=variant,result=result):
                    self.setUp(); calls=[]
                    action=Action(Kind.PREPARE,'run-test',1)
                    class Adapter:
                        def __call__(self, action): calls.append('outer')
                        def observe_url(self, action): calls.append('url')
                    adapter=spans.LiveAdapter(Adapter(),guard=lambda:result)
                    with self.rec.span('workflow'), self.assertRaisesRegex(ValueError,'native_guard_refused'):
                        if variant=='outer': adapter(action)
                        elif variant=='url': adapter.observe_url(action)
                        else: spans.NativeBoundary(self.rec,lambda:result).call('resume_upload','browser',lambda:calls.append('nested'),counters={'uploads':1})
                    self.assertEqual(calls,[])
                    if variant=='nested':
                        self.assertEqual(spans.summarize([],spans.load(self.root))['counters']['uploads'],0)
