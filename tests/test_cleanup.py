import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cleanup


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "applications/runs/run-test"
        self.run_dir.mkdir(parents=True)
        self.records_dir = self.root / "applications/records"
        self.records_dir.mkdir(parents=True)
        self.write(self.run_dir / "run.json", {"run_id": "run-test", "settings": {"external_hosts": {"allow": ["jobs.example"]}}})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def record(self, app_id, status, **extra):
        self.write(self.records_dir / app_id / "record.json", {
            "application_id": app_id, "run_id": "run-test", "status": status, **extra
        })

    def test_only_exact_registered_tabs_are_candidates_and_moved_hosts_are_retained(self):
        registered = [
            {"window_id": 1, "tab_id": 11, "url": "https://www.linkedin.com/jobs/1", "application_id": "app-one"},
            {"window_id": 1, "tab_id": 12, "url": "https://jobs.example/form", "application_id": "app-two"},
        ]
        current = [
            {"window_id": 1, "tab_id": 11, "url": "https://www.linkedin.com/jobs/1"},
            {"window_id": 1, "tab_id": 12, "url": "https://attacker.invalid/form"},
            {"window_id": 1, "tab_id": 99, "url": "https://www.linkedin.com/user-tab"},
        ]
        results = cleanup.close_candidates(registered, current, [{"application_id": "app-one", "status": "ready"}, {"application_id": "app-two", "status": "ready"}], ["linkedin.com", "jobs.example"])
        self.assertEqual([r["action"] for r in results], ["close", "retain"])
        self.assertEqual(results[1]["reason"], "host_moved_outside_allowlist")
        self.assertNotIn(99, [r["tab_id"] for r in results])

    def test_uncertain_tab_retained_and_summary_written_for_stopped_empty_run(self):
        cleanup.register_tab(self.root, "run-test", 2, 21, "https://jobs.example/apply", "app-uncertain", NOW)
        self.record("app-uncertain", "submission_uncertain", application_url="https://jobs.example/apply")
        summary = cleanup.run_summary(self.root, "run-test", NOW)
        self.assertEqual(summary["uncertain_tabs_retained"][0]["tab_id"], 21)
        self.assertTrue((self.run_dir / "summary.json").exists())
        self.assertIn("reconciliation", (self.run_dir / "summary.md").read_text())

        empty = self.root / "applications/runs/stopped-run"
        empty.mkdir()
        self.write(empty / "run.json", {"run_id": "stopped-run", "settings": {}})
        result = cleanup.run_summary(self.root, "stopped-run", NOW)
        self.assertEqual(result["counts"], {"applied": 0, "skipped": 0, "awaiting_answers": 0, "failures": 0})
        self.assertTrue((empty / "summary.json").exists())
        self.assertTrue((empty / "summary.md").exists())

    def test_execute_rechecks_url_before_closing_and_uses_fake_adapter(self):
        cleanup.register_tab(self.root, "run-test", 3, 31, "https://jobs.example/apply", "app-ready", NOW)
        self.record("app-ready", "ready", application_url="https://jobs.example/apply")

        class Adapter:
            def __init__(self):
                self.calls = 0
                self.closed = []

            def enumerate_tabs(self):
                self.calls += 1
                url = "https://jobs.example/apply" if self.calls == 1 else "https://jobs.example/changed"
                return [{"window_id": 3, "tab_id": 31, "url": url},
                        {"window_id": 3, "tab_id": 99, "url": "https://jobs.example/user"}]

            def close_tab(self, window_id, tab_id):
                self.closed.append((window_id, tab_id))

        adapter = Adapter()
        result = cleanup.cleanup_run(self.root, "run-test", NOW, adapter=adapter, execute=True)
        self.assertEqual(adapter.closed, [])
        self.assertEqual(result["decisions"][0]["reason"], "url_changed_before_close")
        self.assertEqual(adapter.calls, 2)

    def test_register_validates_browser_ids(self):
        with self.assertRaises(cleanup.CleanupError):
            cleanup.register_tab(self.root, "run-test", "window one", 1, "https://jobs.example", "app-one", NOW)

    def test_summaries_survive_adapter_failure_and_count_later_responses(self):
        self.record("app-response", "interview", submissions=[{"submitted_at": NOW.isoformat()}])
        adapter = Mock()
        adapter.enumerate_tabs.side_effect = RuntimeError("synthetic adapter unavailable")
        with self.assertRaises(RuntimeError):
            cleanup.cleanup_run(self.root, "run-test", NOW, adapter=adapter, execute=True)
        summary = json.loads((self.run_dir / "summary.json").read_text())
        self.assertEqual(summary["counts"]["applied"], 1)
        self.assertTrue((self.run_dir / "summary.md").exists())

    def test_uncertain_record_from_another_run_and_user_blank_tab_remain_open(self):
        cleanup.register_tab(self.root, "run-test", 1, 4, "https://jobs.example/form", "app-legacy", NOW)
        self.record("app-legacy", "submission_uncertain", run_id="older-run")
        adapter = Mock()
        adapter.enumerate_tabs.return_value = [
            {"window_id": 1, "tab_id": 4, "url": "https://jobs.example/form"},
            {"window_id": 1, "tab_id": 99, "url": "about:blank"}]
        result = cleanup.cleanup_run(self.root, "run-test", NOW, adapter=adapter, execute=True)
        adapter.close_tab.assert_not_called()
        self.assertEqual(result["decisions"][0]["reason"], "submission_uncertain")
        self.assertEqual(result["summary"]["uncertain_tabs_retained"][0]["tab_id"], 4)


if __name__ == "__main__":
    unittest.main()
