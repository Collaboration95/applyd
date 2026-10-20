"""Policy gates compose with capture and preflight before any browser actions."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workspace as w


class SettingsIntegrationTests(unittest.TestCase):
    def test_blocklisted_header_is_logged_without_full_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            w.atomic_json(root / "settings.json", {"company_blocklist": ["Fictional"]})
            result = w.capture(root, {"company": "Fictional Co", "role": "Engineer", "listing_url": "https://www.linkedin.com/jobs/view/555"}, datetime.now(timezone.utc))
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "skipped:blocklist")
            self.assertEqual(w.records(root)[0]["stop_reason"], "skipped:blocklist")

    def test_nonapply_mode_blocks_before_resume_io(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("save_only", "dry_run"):
                w.atomic_json(root / "settings.json", {"mode": mode})
                with self.assertRaisesRegex(ValueError, "mode:"):
                    w.preflight(root, {"status": "ready", "prepared": {"id": "synthetic"}}, datetime.now(timezone.utc))
