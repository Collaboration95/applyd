"""Synthetic capture/contact/export boundary checks."""
import csv
import json
import sys
import subprocess
import test_listing_input as inputs
import unittest
from pathlib import Path
import test_workflow as fixtures
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import workspace as w

class ContactCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root,self.now=self.fixture.root,self.fixture.now
        self.job,self.record=self.fixture.job,self.fixture.record
    def test_card_capture_csv_and_contact_commands(self):
        job=self.job(hiring_team={'name':'Synthetic Poster','headline':'Fictional recruiting lead','profile_url':'https://www.linkedin.com/in/synthetic-poster'})
        app=w.capture(self.root,job,self.now)['application_id']
        contact=self.record(app)['contacts'][0]
        self.assertEqual(contact['captured_at'],self.now.isoformat())
        self.assertEqual(contact['source'],'linkedin_hiring_team')
        self.assertFalse(contact['verified'])
        with (self.root/'applications/log.csv').open() as stream:
            row=list(csv.DictReader(stream))[0]
        self.assertEqual(json.loads(row['contacts']),[contact])
        w.contact_change(self.root,app,self.now,remove=contact['contact_id'])
        self.assertEqual(self.record(app)['contacts'],[])
        w.contact_change(self.root,app,self.now,add={'name':'Another Synthetic Poster','source':'jd_text'})
        self.assertEqual(len(self.record(app)['contacts']),1)

    def test_capture_stdin_normalizes_visible_card_at_real_capture_boundary(self):
        job=inputs.extracted(hiring_team={'name':'Synthetic Poster','headline':'Fixture only','profile_url':'https://www.linkedin.com/in/synthetic-poster'})
        command=[sys.executable,str(Path(w.__file__)), '--workspace', str(self.root), 'capture','--stdin']
        process=subprocess.run(command,input=json.dumps(job),text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr)
        app=json.loads(process.stdout)['application_id']
        record=self.record(app)
        self.assertEqual(record['contacts'][0]['captured_at'],record['captured_at'])
        self.assertEqual(record['contacts'][0]['source'],'linkedin_hiring_team')

    def test_invalid_source_is_rejected_before_record_write(self):
        before=w.records(self.root)
        with self.assertRaisesRegex(ValueError,'source'):
            w.capture(self.root,self.job(contacts=[{'name':'Synthetic','source':'enrichment_service'}]),self.now)
        self.assertEqual(w.records(self.root),before)
