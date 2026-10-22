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
const t=T.create({context:{run_id:'run-test',application_id:'synthetic-app',attempt_id:'attempt',action_id:'1'},processId:'synthetic-js-process',monotonic:()=>seconds,wall:()=>new Date(Date.parse('2026-10-20T01:00:00Z')+seconds*1000),emit:r=>rows.push(r)});
(async()=>{await t.call('answer_resolution','model',()=>{seconds=2;return {output_tokens:12};},{model:{provider:'synthetic',model:'fixture',configuration:'default'},outputUsage:r=>r.output_tokens});console.log(JSON.stringify(rows));})();
"""
        script=Path(__file__).resolve().parents[1]/'scripts/telemetry.js'
        rows=json.loads(subprocess.run(['node','-e',source,str(script)],capture_output=True,text=True,check=True,timeout=5).stdout)
        for row in rows:self.assertTrue(self.rec.ingest(row))
        report=spans.summarize([],spans.load(self.root))
        self.assertEqual(report['output_tokens_per_second'],6)
        self.assertEqual(report['measured_spans'],1)
        self.assertEqual(spans.quantiles([1,2,3,4]),{'samples':4,'median_seconds':2.5,'p95_seconds':4})
