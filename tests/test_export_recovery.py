"""Export recovery tests use only synthetic records in temporary directories."""

import csv
import io
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_recovery as recovery


class ExportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="export-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshots = 0

    def mutate(self, root, status):
        """A synthetic committed action; recovery never receives this function."""
        path = root / "applications/records/app-example/record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.loads(path.read_text()) if path.exists() else {
            "application_id": "app-example", "events": [], "submissions": []}
        record["status"] = status
        record["events"].append({"at": "2026-10-20T12:00:00+08:00", "status": status})
        if status == "submitted":
            record["submissions"].append({"confirmation": "synthetic confirmation"})
        path.write_text(json.dumps(record), encoding="utf-8")
        return {"application_id": "app-example", "status": status}

    def record_bytes(self, root=None):
        return ((root or self.root) / "applications/records/app-example/record.json").read_bytes()

    def exporter(self, root):
        # Parent integration owns this single scan, shared by every output.
        self.snapshots += 1
        snapshot = [json.loads(path.read_text(encoding="utf-8")) for path in
                    sorted((root / "applications/records").glob("*/record.json"))]
        stream = io.StringIO(newline="")
        output = csv.writer(stream)
        output.writerow(["application_id", "status"])
        output.writerows((record["application_id"], record["status"]) for record in snapshot)
        (root / "applications/applications.csv").write_text(stream.getvalue(), encoding="utf-8")
        payload = json.dumps({"applications": snapshot}, sort_keys=True)
        directory = root / "dashboard/data"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "applications.json").write_text(payload, encoding="utf-8")
        (root / "dashboard/data.js").write_text("window.JOB_SEARCH_DATA = " + payload + ";\n", encoding="utf-8")
        return {"records": len(snapshot)}

    def outputs(self, root):
        return [(root / name).read_bytes() for name in (
            "applications/applications.csv", "dashboard/data/applications.json", "dashboard/data.js")]

    def test_export_failure_preserves_mutation_and_redacts_error(self):
        result = self.mutate(self.root, "submitted")
        before = self.record_bytes()

        def failed(root):
            self.assertEqual(recovery.export_state(root)["status"], "pending")
            # A CSV can succeed while the dashboard fails: keep all exports dirty.
            (root / "applications/applications.csv").write_text("partial synthetic CSV")
            raise OSError("synthetic-private-contact@example.invalid /private/synthetic/person")

        outcome = recovery.after_mutation(self.root, result, failed)
        self.assertEqual(outcome["mutation"], {**result, "ok": True})
        self.assertEqual(outcome["export"]["error_code"], "export_failed")
        self.assertFalse(outcome["export"]["ok"])
        self.assertEqual(self.record_bytes(), before)
        self.assertEqual(recovery.export_state(self.root)["status"], "failed")
        shared = json.dumps(outcome) + (self.root / recovery.STATE_PATH).read_text()
        self.assertNotIn("synthetic-private-contact", shared)
        self.assertNotIn("/private/synthetic/person", shared)

    def test_recovery_twice_repairs_outputs_without_duplicate_events_or_submissions(self):
        result = self.mutate(self.root, "submitted")
        recovery.after_mutation(self.root, result, Mock(side_effect=RuntimeError("synthetic failure")))
        before = self.record_bytes()
        for _ in range(2):
            outcome = recovery.recover(self.root, self.exporter)
            self.assertEqual(outcome, {"ok": True, "status": "exported", "result": {"records": 1}})
            self.assertEqual(self.record_bytes(), before)
        record = json.loads(before)
        self.assertEqual(len(record["events"]), 1)
        self.assertEqual(len(record["submissions"]), 1)
        self.assertEqual(self.snapshots, 2)
        self.assertFalse(recovery.export_state(self.root)["dirty"])
        self.assertEqual(json.loads(self.outputs(self.root)[1])["applications"], [record])

    def test_default_auto_export_and_deferred_batch_have_equivalent_final_outputs(self):
        deferred_root = self.root / "deferred"
        never_export = Mock(side_effect=AssertionError("Deferred exporter must not run"))
        for status in ("discovered", "preparing", "submitted"):
            result = self.mutate(self.root, status)
            automatic = recovery.after_mutation(self.root, result, self.exporter)
            self.assertTrue(automatic["export"]["ok"])
            result = self.mutate(deferred_root, status)
            deferred = recovery.after_mutation(deferred_root, result, never_export, deferred=True)
            self.assertIsNone(deferred["export"]["ok"])
            self.assertEqual(recovery.export_state(deferred_root)["status"], "deferred")
        never_export.assert_not_called()
        self.assertFalse((deferred_root / "dashboard/data.js").exists())
        self.assertEqual(self.snapshots, 3)
        self.assertTrue(recovery.recover(deferred_root, self.exporter)["ok"])
        self.assertEqual(self.snapshots, 4)
        self.assertEqual(self.outputs(self.root), self.outputs(deferred_root))
        self.assertEqual(self.record_bytes(), self.record_bytes(deferred_root))

    def test_recovery_rebuilds_missing_outputs_even_with_clean_marker(self):
        self.mutate(self.root, "discovered")
        recovery.recover(self.root, self.exporter)
        expected = self.outputs(self.root)
        (self.root / "dashboard/data.js").unlink()
        self.assertTrue(recovery.recover(self.root, self.exporter)["ok"])
        self.assertEqual(self.outputs(self.root), expected)

    def test_retry_command_selects_exact_root_and_only_exports(self):
        root = self.root / "synthetic workspace 'quoted'"
        result = self.mutate(root, "discovered")
        outcome = recovery.after_mutation(root, result, Mock(side_effect=ValueError("synthetic")))
        self.assertEqual(shlex.split(outcome["export"]["retry_command"]), [
            "python3", "scripts/workspace.py", "--workspace", str(root.resolve()), "export"])

    def test_marker_write_failure_does_not_erase_mutation_success(self):
        result = self.mutate(self.root, "submitted")
        before = self.record_bytes()
        for deferred in (False, True):
            exporter = Mock()
            with patch.object(recovery, "_write_state", side_effect=OSError("synthetic private error")):
                outcome = recovery.after_mutation(self.root, result, exporter, deferred=deferred)
            self.assertTrue(outcome["mutation"]["ok"])
            self.assertEqual(outcome["export"]["error_code"], "export_state_write_failed")
            exporter.assert_not_called()
            self.assertEqual(self.record_bytes(), before)

    def test_clean_marker_write_failure_keeps_pending_marker(self):
        self.mutate(self.root, "submitted")
        write = recovery._write_state

        def fail_clean(root, status):
            if status == "clean":
                raise OSError("synthetic failure")
            write(root, status)

        with patch.object(recovery, "_write_state", side_effect=fail_clean):
            outcome = recovery.recover(self.root, self.exporter)
        self.assertEqual(outcome["error_code"], "export_state_write_failed")
        self.assertEqual(recovery.export_state(self.root)["status"], "pending")
        self.assertTrue(recovery.recover(self.root, self.exporter)["ok"])

    def test_interruption_keeps_durable_dirty_marker(self):
        result = self.mutate(self.root, "submitted")
        before = self.record_bytes()
        with self.assertRaises(KeyboardInterrupt):
            recovery.after_mutation(self.root, result, Mock(side_effect=KeyboardInterrupt))
        self.assertTrue(recovery.export_state(self.root)["dirty"])
        self.assertEqual(self.record_bytes(), before)

    def test_export_state_is_conservative_and_drops_unexpected_fields(self):
        self.assertEqual(recovery.export_state(self.root)["status"], "unknown")
        path = self.root / recovery.STATE_PATH
        path.parent.mkdir(parents=True)
        for content in ("not JSON", "[]", '{"schema_version":1,"dirty":false,"status":"failed"}',
                        '{"schema_version":1,"dirty":false,"status":[]}',
                        '{"schema_version":1,"dirty":false,"status":"unknown"}'):
            path.write_text(content)
            self.assertTrue(recovery.export_state(self.root)["dirty"])
        path.write_text(json.dumps({"schema_version": 1, "dirty": False,
                                    "status": "clean", "error": "synthetic private detail"}))
        self.assertEqual(recovery.export_state(self.root), {
            "schema_version": 1, "dirty": False, "status": "clean"})

    def test_input_result_is_unchanged_and_cannot_override_success(self):
        result = {"application_id": "app-example", "ok": False}
        original = result.copy()
        outcome = recovery.after_mutation(self.root, result, lambda root: {}, deferred=True)
        self.assertEqual(result, original)
        self.assertTrue(outcome["mutation"]["ok"])


if __name__ == "__main__":
    unittest.main()
