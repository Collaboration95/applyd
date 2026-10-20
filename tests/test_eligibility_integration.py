"""Synthetic early exclusion and execution-boundary checks."""
from datetime import timedelta
from pathlib import Path
import unittest
import test_workflow as fixtures
import workspace as w

class EligibilityIntegrationTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    event=fixtures.WorkflowTests.event
    capture=fixtures.WorkflowTests.capture
    ready=fixtures.WorkflowTests.ready

    def test_text_exclusion_before_prepare_and_ambiguity_remains_reviewable(self):
        w.atomic_json(self.root/'settings.json',{'max_required_years':3})
        result=w.capture(self.root,self.job(job_description='Minimum 4 years of experience required.'),self.now)
        self.assertEqual(result['status'],'skipped')
        with self.assertRaisesRegex(ValueError,'eligibility_excluded'):
            self.event(result['application_id'],'preparing')
        other=w.capture(self.root,self.job(company='Another',platform_job_id='987654',listing_url='https://www.linkedin.com/jobs/view/987654',job_description='Around 4 years of experience.'),self.now)
        self.assertEqual(other['status'],'needs_review')
        self.assertIn('experience_ambiguous',self.record(other['application_id'])['eligibility']['reason_codes'])

    def test_preflight_rechecks_current_and_frozen_policy_and_description(self):
        app=self.ready()
        before=self.record(app)['prepared']
        w.atomic_json(self.root/'settings.json',{'company_blocklist':['Example']})
        with self.assertRaisesRegex(ValueError,'blocklist'):
            w.preflight(self.root,self.record(app),self.now)
        self.assertEqual(self.record(app)['prepared'],before)
        w.atomic_json(self.root/'settings.json',{})
        description=self.root/self.record(app)['job_description_path']
        description.write_text('Changed synthetic text')
        with self.assertRaisesRegex(ValueError,'description changed'):
            w.preflight(self.root,self.record(app),self.now)

    def test_confirmed_sponsorship_fact_requires_explicit_country(self):
        w.atomic_json(self.root/'profile/answers.json',{'answers':[{'id':'sponsorship_required','value':True,'type':'boolean','kind':'sponsorship','scope':{'country':'Exampleland'},'confirmed_at':(self.now-timedelta(days=1)).isoformat(),'source':'Synthetic user'}]})
        result=w.capture(self.root,self.job(country='Exampleland',job_description='We cannot sponsor visas.'),self.now)
        self.assertEqual(result['status'],'skipped')
        self.assertEqual(result['reason'],'sponsorship_unavailable')
