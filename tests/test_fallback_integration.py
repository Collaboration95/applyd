"""Workspace gates with synthetic fallback adapters; no browser operations."""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_workflow as fixtures
import test_computer_fallback as fallback_fixtures
import workspace as w

class FallbackIntegrationTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    event=fixtures.WorkflowTests.event
    capture=fixtures.WorkflowTests.capture
    ready=fixtures.WorkflowTests.ready
    evidence=fixtures.WorkflowTests.evidence

    def setup_fallback(self):
        w.atomic_json(self.root/'settings.json',{'mode':'apply','resume_scope':'backend', 'external_hosts':{'allow':['example.test'],'deny':[]},'computer_use':{'enabled':True,'step_budget':1}})
        w.settings.run_start(self.root,'fixture-run',self.now)
        app=w.capture(self.root,self.job(run_id='fixture-run',easy_apply=False,application_url=fallback_fixtures.URL),self.now)['application_id']
        w.prepare_answers(self.root,app,[],self.now)
        return app,{'initial_tab_id':fallback_fixtures.TAB,'initial_url':fallback_fixtures.URL,'failure_kind':'custom_widget','dom_failed':True,'actions':[{'kind':'select_option','target':'fixture-language','value':'Python'}]}

    def test_parent_guard_and_durable_session_complete_once(self):
        app,plan=self.setup_fallback()
        adapter=fallback_fixtures.FakeAdapter(self.root)
        result=w.fallback_session(self.root,app,'fixture-run','fixture-session',plan,self.now,adapter=adapter,clock=lambda:self.now)
        self.assertEqual(result['outcome'],'completed')
        self.assertEqual(result['step_count'],1)
        w.fallback_session(self.root,app,'fixture-run','fixture-session',plan,self.now,adapter=adapter,clock=lambda:self.now)
        self.assertEqual(len(adapter.performed),1)

    def test_live_browser_stop_prevents_adapter_interaction(self):
        app,plan=self.setup_fallback()
        w.retry_failure(self.root,app,'login',self.now)
        adapter=fallback_fixtures.FakeAdapter(self.root)
        result=w.fallback_session(self.root,app,'fixture-run','fixture-session',plan,self.now,adapter=adapter,clock=lambda:self.now)
        self.assertEqual(result['outcome'],'failed')
        self.assertEqual(adapter.performed,[])
        self.assertEqual(adapter.observations,0)

    def test_current_quota_tightening_stops_widgets_before_interaction(self):
        app,plan=self.setup_fallback()
        policy=w.settings.load_settings(self.root);policy['per_run_cap']=0
        w.atomic_json(self.root/'settings.json',policy)
        adapter=fallback_fixtures.FakeAdapter(self.root)
        result=w.fallback_session(self.root,app,'fixture-run','fixture-session',plan,self.now,adapter=adapter,clock=lambda:self.now)
        self.assertEqual(result['outcome'],'failed')
        self.assertEqual(adapter.observations,0)
        self.assertEqual(adapter.performed,[])

    def test_submit_refuses_existing_fallback_writer(self):
        app=self.ready()
        lock=self.root/'applications/computer-use.lock';lock.write_text('synthetic writer')
        with self.assertRaisesRegex(ValueError,'browser_claimed'):
            w.begin_submit(self.root,app,self.record(app)['listing_url'],'fixture',self.now)
        self.assertFalse((self.root/'applications/browser-claim.json').exists())

    def test_writer_race_preserves_claim_for_uncertainty_recovery(self):
        app=self.ready()
        original=w.os.open
        def raced(path,*args,**kwargs):
            fd=original(path,*args,**kwargs)
            if Path(path).name=='browser-claim.json':
                (self.root/'applications/computer-use.lock').write_text('synthetic race')
            return fd
        with patch.object(w.os,'open',side_effect=raced):
            with self.assertRaisesRegex(ValueError,'raced'):
                w.begin_submit(self.root,app,self.record(app)['listing_url'],'fixture',self.now)
        self.assertTrue((self.root/'applications/browser-claim.json').exists())
        w.recover_submit(self.root,self.now)
        self.assertEqual(self.record(app)['status'],'submission_uncertain')
