"""Configured retry limits and immutable legacy-history compatibility."""
import unittest
from pathlib import Path
import test_workflow as fixtures
import workspace as w
from runner_workspace import build_runner
from runner import Kind

class BudgetWorkspaceTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    event=fixtures.WorkflowTests.event
    capture=fixtures.WorkflowTests.capture
    ready=fixtures.WorkflowTests.ready
    evidence=fixtures.WorkflowTests.evidence

    def test_workspace_retry_calls_respect_tighter_current_and_frozen_limits(self):
        w.atomic_json(self.root/'settings.json',{'work_budget':{'max_retries':1}})
        w.settings.run_start(self.root,'budget-run',self.now)
        app=w.capture(self.root,self.job(run_id='budget-run'),self.now)['application_id']
        w.retry_failure(self.root,app,'timeout',self.now)
        self.now=w.timestamp(self.record(app)['next_eligible_at'])
        w.atomic_json(self.root/'settings.json',{'work_budget':{'max_retries':9}})
        w.retry_failure(self.root,app,'timeout',self.now)
        self.assertEqual(w.retry_status(self.root,app,self.now)['decision'],'exhausted')
        self.assertEqual(self.record(app)['retry_budget'],1)
        self.assertEqual(self.record(app)['retry_counts']['timeout'],2)
        with self.assertRaisesRegex(ValueError,'exhausted'):
            w.retry_guard(self.record(app),self.now,root=self.root)

    def test_legacy_confirmed_run_metadata_never_blocks_new_work_or_changes_bytes(self):
        app=self.ready();self.event(app,'submitted',evidence=self.evidence())
        record=self.record(app);record['run_id']='legacy-history';w.persist_record(self.root,record)
        path=self.root/'applications/records'/app/'record.json';before=path.read_bytes()
        w.atomic_json(self.root/'settings.json',{'resume_scope':'backend'})
        w.settings.run_start(self.root,'new-run',self.now)
        w.work_guard(self.root,'new-run',self.now,'browser-guard')
        other=w.capture(self.root,self.job(company='New Synthetic',role='New Engineer',platform_job_id='909',listing_url='https://www.linkedin.com/jobs/view/909',run_id='new-run',batch_id='new-batch'),self.now)['application_id']
        brief=self.root/'new-brief.json';w.atomic_json(brief,{'batch_id':'new-batch','target':1})
        planned=build_runner(self.root,'new-run',brief).plan_next(self.now)
        self.assertEqual(planned.kind,Kind.PREPARE)
        self.assertEqual(planned.application_id,other)
        self.assertEqual(path.read_bytes(),before)
