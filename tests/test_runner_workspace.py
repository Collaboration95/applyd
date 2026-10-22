"""Synthetic end-to-end parent hooks; no real browser or applications."""
import argparse
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import unittest
import test_workflow as fixtures
import workspace as w
import spans
from runner import Kind, Observation
from runner_workspace import build_runner

class RunnerWorkspaceTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    evidence=fixtures.WorkflowTests.evidence

    def initialize(self):
        w.atomic_json(self.root/'settings.json',{'resume_scope':'backend'})
        w.settings.run_start(self.root,'fixture-run',self.now)
        self.brief=self.root/'brief.json';w.atomic_json(self.brief,{'batch_id':'fixture-batch','target':1})
        self.app=w.capture(self.root,self.job(run_id='fixture-run',batch_id='fixture-batch'),self.now)['application_id']
        w.atomic_json(self.root/'profile/answers.json',{'answers':[]})

    def test_plan_and_cli_no_adapter_never_fabricate_execution(self):
        self.initialize()
        controller=build_runner(self.root,'fixture-run',self.brief)
        self.assertEqual(controller.plan_next(self.now).kind,Kind.PREPARE)
        self.assertEqual(controller.execute(clock=lambda:self.now)[0].reason,'adapter_missing')
        self.assertIsNone(w.read(self.root/'applications/runs/fixture-run/state.json')['checkpoint'])
        process=subprocess.run([sys.executable,str(Path(w.__file__)),'--workspace',str(self.root),'next-action','--run-id','fixture-run','--batch',str(self.brief)],capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertIn('"kind": "prepare"',process.stdout)

    def test_fixture_prepare_review_confirm_restart_quota_and_no_second_click(self):
        self.initialize();outer=self
        seconds = [0]
        class Adapter:
            clicks=0
            def observe_url(self,action):
                seconds[0] += 2
                return outer.record(action.application_id)['listing_url']
            def __call__(self,action):
                app=action.application_id
                if action.kind==Kind.PREPARE:
                    result=w.prepare_answers(outer.root,app,[],outer.now,deferred=True)
                    args=argparse.Namespace(id=app,status='ready',note='',resume_scope='backend',answers=outer.root/result['answers_path'],evidence=None)
                    w.event(outer.root,args,outer.now,deferred=True)
                elif action.kind==Kind.REVIEW:
                    prepared=outer.record(app)['prepared']
                    w.review_form(outer.root,app,{'attempt_id':prepared['attempt_id'],'resume_sha256':prepared['sha256'],'fields':[]},outer.now,deferred=True)
                elif action.kind==Kind.SUBMIT:
                    self.clicks+=1
                    args=argparse.Namespace(id=app,status='submitted',note='',resume_scope=None,answers=None,evidence=outer.evidence())
                    w.event(outer.root,args,outer.now,deferred=True)
                return Observation()
        adapter=Adapter();controller=build_runner(self.root,'fixture-run',self.brief,adapter,monotonic=lambda:seconds[0])
        results=controller.execute(clock=lambda:self.now+timedelta(seconds=seconds[0]),max_steps=5)
        self.assertEqual([x.kind for x in results],[Kind.PREPARE,Kind.REVIEW,Kind.SUBMIT,Kind.COMPLETE])
        self.assertEqual(adapter.clicks,1)
        rows = spans.load(self.root)
        observed = [r for r in rows if r['phase']=='preflight' and r['channel']=='browser']
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0]['duration_seconds'],2)
        self.assertEqual(observed[0]['application_id'],self.app)
        self.assertIsNotNone(observed[0]['attempt_id'])
        self.assertIsNotNone(observed[0]['parent_id'])
        summary = spans.summarize(w.records(self.root), rows)
        self.assertEqual(summary['critical_path_active_seconds'],2)
        resumed=build_runner(self.root,'fixture-run',self.brief,adapter)
        self.assertEqual(resumed.execute(clock=lambda:self.now)[0].kind,Kind.COMPLETE)
        self.assertEqual(adapter.clicks,1)
        self.assertEqual(self.record(self.app)['status'],'submitted')

    def test_unknown_questions_checkpoint_wait_then_continue_independent_job(self):
        self.initialize();outer=self
        other=w.capture(self.root,self.job(company='Other Synthetic',role='Other Engineer',platform_job_id='987',listing_url='https://www.linkedin.com/jobs/view/987',run_id='fixture-run',batch_id='fixture-batch'),self.now)['application_id']
        class Adapter:
            blocked=None
            def __call__(self,action):
                if self.blocked is None:
                    self.blocked=action.application_id
                    w.prepare_answers(outer.root,self.blocked,[{'question':'Unconfirmed synthetic qualification?'}],outer.now,deferred=True)
                return Observation()
        adapter=Adapter();controller=build_runner(self.root,'fixture-run',self.brief,adapter)
        self.assertEqual(controller.execute(clock=lambda:self.now)[0].kind,Kind.PREPARE)
        self.assertEqual(self.record(adapter.blocked)['status'],'awaiting_answer')
        next_action=controller.plan_next(self.now)
        self.assertEqual(next_action.kind,Kind.PREPARE)
        self.assertNotEqual(next_action.application_id,adapter.blocked)
        self.assertIsNone(w.read(self.root/'applications/runs/fixture-run/state.json')['pending'])

    def test_unverified_submit_stays_pending_and_never_replays(self):
        self.initialize();outer=self
        # Reuse the exact synthetic ready/review helper without another capture.
        prepared=w.prepare_answers(self.root,self.app,[],self.now)
        w.event(self.root,argparse.Namespace(id=self.app,status='ready',note='',resume_scope='backend',answers=self.root/prepared['answers_path'],evidence=None),self.now)
        attempt=self.record(self.app)['prepared'];w.review_form(self.root,self.app,{'attempt_id':attempt['attempt_id'],'resume_sha256':attempt['sha256'],'fields':[]},self.now)
        class Adapter:
            calls=0
            def observe_url(self,action):return outer.record(action.application_id)['listing_url']
            def __call__(self,action):self.calls+=1;raise TimeoutError('Synthetic interruption')
        adapter=Adapter();controller=build_runner(self.root,'fixture-run',self.brief,adapter)
        self.assertEqual(controller.execute(clock=lambda:self.now)[0].reason,'submission_uncertain')
        self.assertEqual(self.record(self.app)['status'],'submission_uncertain')
        self.assertEqual(build_runner(self.root,'fixture-run',self.brief,adapter).execute(clock=lambda:self.now)[0].reason,'submission_uncertain')
        self.assertEqual(adapter.calls,1)
