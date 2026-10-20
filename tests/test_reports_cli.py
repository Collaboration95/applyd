"""Read-only CLI reporting with synthetic sensitive canaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import workspace as w
from test_reports import application

class ReportsCliTests(unittest.TestCase):
    def test_reproducible_shareable_week_and_all_time_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            marker='PRIVATE'+'_CANARY_'+'REPORT_CLI'
            record=application('synthetic-record',company=marker,preset=marker,tags=[marker],blockers=[marker])
            w.atomic_json(root/'applications/records/synthetic-record/record.json',record)
            before=(root/'applications/records/synthetic-record/record.json').read_bytes()
            command=[sys.executable,str(Path(w.__file__)),'--workspace',str(root)]
            outputs=[]
            for _ in range(2):
                result=subprocess.run(command+['report','--week','2026-W43','--shareable','--json'],text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn(marker,result.stdout)
                outputs.append(result.stdout)
            self.assertEqual(outputs[0],outputs[1])
            report=json.loads(outputs[0]);self.assertEqual(report['totals']['applications'],1)
            self.assertIsNone(report['totals']['median_effort_seconds'])
            result=subprocess.run(command+['metrics'],text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(marker,result.stdout)
            self.assertEqual(json.loads(result.stdout)['unique_confirmed_applications'],1)
            self.assertEqual((root/'applications/records/synthetic-record/record.json').read_bytes(),before)
