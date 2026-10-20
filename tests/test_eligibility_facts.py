"""Synthetic saved-fact scope and freshness checks."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import eligibility

class EligibilityFactsTests(unittest.TestCase):
    def test_current_scoped_boolean_only_and_no_inference(self):
        now=datetime(2026,10,20,tzinfo=timezone.utc)
        entry={'id':'sponsorship_required','value':True,'type':'boolean','kind':'sponsorship','scope':{'country':'Exampleland'},'source':'Synthetic user','confirmed_at':(now-timedelta(days=1)).isoformat()}
        listing={'country':'Exampleland','application_id':'fixture','company':'Example'}
        self.assertTrue(eligibility.confirmed_facts({'answers':[entry]},listing,now)['sponsorship_required']['value'])
        for update in ({'value':'Yes'},{'source':None},{'review_due_at':now.isoformat()},{'confirmed_at':(now+timedelta(days=1)).isoformat()},{'scope':{'country':'Otherland'}},{'scope':{}}):
            self.assertEqual(eligibility.confirmed_facts({'answers':[{**entry,**update}]},listing,now),{})
        self.assertEqual(eligibility.confirmed_facts({'answers':[entry,entry]},listing,now),{})
        self.assertEqual(eligibility.confirmed_facts({'answers':[entry]}, {'location':'Exampleland','student_pass':True},now),{})
