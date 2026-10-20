"""One synthetic release scenario; never opens a browser or submits a real form."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import workspace as w
import answers
import batches
from export_recovery import recover
import test_workflow as fixtures


class ReleaseSmokeTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    add_release=fixtures.WorkflowTests.add_release
    save_registry=fixtures.WorkflowTests.save_registry
    job=fixtures.WorkflowTests.job
    record=fixtures.WorkflowTests.record
    event=fixtures.WorkflowTests.event
    evidence=fixtures.WorkflowTests.evidence

    def prepare_and_review(self, app):
        result=w.prepare_answers(self.root,app,[{'question':'Synthetic shared qualification?'}],self.now)
        self.assertTrue(result['ready'])
        self.event(app,'ready',resume_scope='backend',answers=self.root/result['answers_path'])
        prepared=self.record(app)['prepared']
        # This is an explicit synthetic browser observation, never live evidence.
        rows=w.read(self.root/prepared['answers_path'])
        visible={'attempt_id':prepared['attempt_id'],'resume_sha256':prepared['sha256'],
                 'fields':[{'occurrence_id':rows[0]['occurrence_id'],'question':'Synthetic shared qualification?','type':'text','value':'Yes'}]}
        self.assertTrue(w.review_form(self.root,app,visible,self.now)['passed'])
        return self.record(app)['prepared']

    def test_discover_shared_answer_resume_uncertainty_confirmation_and_export_recovery(self):
        w.atomic_json(self.root/'settings.json',{'resume_scope':'backend','company_blocklist':['Blocked Synthetic']})
        w.settings.run_start(self.root,'release-run',self.now)
        original_resume=(self.root/self.application['path']).read_bytes()
        apps=[]
        for number,company in [('123456','Synthetic First'),('987654','Synthetic Second')]:
            result=w.capture(self.root,self.job(company=company,platform_job_id=number,listing_url='https://www.linkedin.com/jobs/view/'+number,run_id='release-run',batch_id='release-batch'),self.now)
            app=result['application_id'];apps.append(app)
            prepared=w.prepare_answers(self.root,app,[{'question':'Synthetic shared qualification?'}],self.now)
            self.assertFalse(prepared['ready'])
            self.assertEqual(self.record(app)['status'],'awaiting_answer')
        self.assertEqual(self.record(apps[0])['blocked_on_answer_ids'],self.record(apps[1])['blocked_on_answer_ids'])
        skipped=w.capture(self.root,self.job(company='Blocked Synthetic',platform_job_id='654321',listing_url='https://www.linkedin.com/jobs/view/654321',run_id='release-run'),self.now)
        self.assertEqual(skipped['status'],'skipped')
        bank=answers._load_bank(self.root)
        change={'schema_version':1,'import_id':'release-answer-import','base_version':answers.bank_version(bank),'changes':[{'change_id':'release-shared-answer','answer':{'id':'synthetic-shared-answer','value':'Yes','type':'text','scope':{},'aliases':['Synthetic shared qualification?'],'source':'Synthetic release test confirmation','confirmed_at':self.now.isoformat()}}]}
        imported=answers.import_answers(self.root,change,self.now)
        self.assertEqual(len(imported['applied_change_ids']),1)
        self.assertTrue(answers.import_answers(self.root,change,self.now)['already_imported'])
        queued=w.requeue_answers(self.root,self.now)
        self.assertEqual(set(queued['runnable_ids']),set(apps))
        self.assertEqual(len(answers._load_bank(self.root)['answers']),1)
        first=apps[0];old=self.prepare_and_review(first)
        frozen={key:(self.root/old[key]).read_bytes() for key in ('exact_resume_copy','answers_path','review_snapshot')}
        self.assertTrue(w.begin_submit(self.root,first,self.record(first)['listing_url'],'fixture-writer',self.now)['click_authorized'])
        self.assertTrue(w.recover_submit(self.root,self.now)['requires_reconciliation'])
        with self.assertRaises(ValueError):
            w.begin_submit(self.root,first,self.record(first)['listing_url'],'fixture-writer',self.now)
        not_executed=self.root/'not-executed.txt';not_executed.write_text('Synthetic adapter observed no Submit operation')
        self.event(first,'failed',evidence=not_executed,note='Synthetic adapter verified the interrupted click did not occur')
        self.event(first,'preparing')
        self.prepare_and_review(first)
        for key,content in frozen.items():self.assertEqual((self.root/old[key]).read_bytes(),content)
        self.assertTrue(w.begin_submit(self.root,first,self.record(first)['listing_url'],'fixture-writer',self.now)['click_authorized'])
        marker='PRIVATE'+'_CANARY_'+'RELEASE_FIXTURE'
        with patch.object(w,'export_csv',side_effect=OSError(marker)):
            confirmed=self.event(first,'submitted',evidence=self.evidence())
        self.assertTrue(confirmed['mutation']['ok'])
        self.assertFalse(confirmed['export']['ok'])
        self.assertNotIn(marker,json.dumps(confirmed))
        self.assertEqual(self.record(first)['status'],'submitted')
        self.assertTrue(recover(self.root,w.export_csv)['ok'])
        self.prepare_and_review(apps[1])
        w.begin_submit(self.root,apps[1],self.record(apps[1])['listing_url'],'fixture-writer',self.now)
        self.event(apps[1],'submitted',evidence=self.evidence())
        before=[len(self.record(app)['submissions']) for app in apps]
        self.assertTrue(recover(self.root,w.export_csv)['ok'])
        self.assertEqual([len(self.record(app)['submissions']) for app in apps],before)
        duplicate=w.capture(self.root,self.job(company='Synthetic First',run_id='release-run'),self.now)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(batches.batch_progress(w.records(self.root),'release-batch',2)['remaining'],0)
        with (self.root/'applications/log.csv').open() as stream:self.assertEqual(len(list(csv.DictReader(stream))),3)
        self.assertEqual((self.root/self.application['path']).read_bytes(),original_resume)
        data=w.read(self.root/'dashboard/data/applications.json')
        self.assertEqual(len(data['applications']),3)
        self.assertEqual(sum(bool(app['submissions']) for app in data['applications']),2)

    def test_fresh_init_doctor_and_fictional_dashboard(self):
        import setup_workspace
        import demo
        with tempfile.TemporaryDirectory(prefix='applyd-release-') as folder:
            base=Path(folder);root=base/'workspace'
            setup_workspace.init_workspace(root)
            report=setup_workspace.doctor(root)
            self.assertIsInstance(report,dict)
            payload=demo.build(base/'demo','2026-10-20T12:00:00+08:00')
            self.assertTrue(payload.is_file())
            data=w.read(base/'demo/dashboard/data/applications.json')
            self.assertEqual(len(data['applications']),15)
            for asset in ('app.js','control.js','runs.js','style.css'):
                self.assertTrue((base/'demo/dashboard'/asset).is_file())
            self.assertEqual(w.read(root/'profile/answers.json')['answers'],[])


if __name__=='__main__':unittest.main()
