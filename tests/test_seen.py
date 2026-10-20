"""Filesystem identity lookups use synthetic temporary workspaces only."""
import argparse
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workspace as w
import seen_index as index


class SeenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.job = dict(source="linkedin_job_page", company="Fictional Co", role="Engineer", location="Example City", platform="linkedin",
                        platform_job_id="123", employer_requisition_id="REQ-1",
                        listing_url="https://www.linkedin.com/jobs/view/123", job_description="Synthetic full text")
        self.app = w.capture(self.root, dict(self.job), self.now)["application_id"]

    def markers(self):
        return {p.name: p.read_bytes() for p in (self.root / "applications/index").iterdir()}

    def test_all_identities_and_unknown(self):
        canonical = w.canonical_url(self.job["listing_url"])
        keys = index.identity_keys(self.job, canonical)
        self.assertEqual(len(keys), 4)
        shell = Path(w.__file__).with_name("seen.sh")
        result = subprocess.run([str(shell), "123", *keys, "999"], env={**os.environ, "APPLYD_WORKSPACE": str(self.root)}, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[0], f"123 discovered {self.app}")
        self.assertEqual(result.stdout.splitlines()[-1], "999 new")

    def test_events_and_reindex_byte_identical_remove_stale(self):
        w.event(self.root, argparse.Namespace(id=self.app, status="preparing", note=""), self.now)
        before = self.markers()
        (self.root / "applications/index/li-999").write_text("stale discovered\n")
        index.reindex(self.root, w.records(self.root), w.canonical_url)
        self.assertEqual(before, self.markers())
        self.assertTrue(all(b"preparing" in v for k,v in before.items() if not k.startswith(".")))

    def test_duplicate_does_not_scan_records(self):
        with patch.object(w, "records", side_effect=AssertionError("record scan")):
            self.assertTrue(w.capture(self.root, dict(self.job), self.now)["duplicate"])

    def test_batch_lookup_under_twenty_ms_without_python(self):
        shell = Path(w.__file__).with_name("seen.sh")
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            subprocess.run([str(shell), *map(str, range(25))], env={"APPLYD_WORKSPACE": str(self.root), "PATH": "/nonexistent"}, stdout=subprocess.DEVNULL, check=True)
            timings.append(time.perf_counter() - start)
        self.assertLess(min(timings), .020)


if __name__ == "__main__":
    unittest.main()
