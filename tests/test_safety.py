"""Safety tests use isolated synthetic workspaces; no browser or notifications."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import safety as s


class SafetyTests(unittest.TestCase):
    def test_workspace_and_shell_stop_hooks_refuse_new_work(self):
        import workspace as w
        (self.root / s.STOP).touch()
        with patch.object(s, "local_notification") as notify:
            with self.assertRaises(s.SafetyBlocked):
                w.capture(self.root, {"run_id": "one"}, self.now)
            with self.assertRaises(s.SafetyBlocked):
                w.preflight(self.root, {"run_id": "one"}, self.now)
            self.assertEqual(notify.call_count, 1)
        shell = Path(w.__file__).with_name("seen.sh")
        import os
        result = subprocess.run([str(shell), "123"], env={**os.environ, "APPLYD_WORKSPACE": str(self.root)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.root / "applications/runs/one/safety.json").exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "applications").mkdir()
        self.now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
        self.notify = Mock()

    def screenshot(self):
        path = self.root / "applications/evidence/synthetic.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Adapter fixture, not a claim that any live screenshot was captured.
        path.write_bytes(b"synthetic screenshot fixture; never published")
        return {"screenshot": path.relative_to(self.root).as_posix(), "kind": "challenge"}

    def read(self, relative):
        return json.loads((self.root / relative).read_text())

    def stop(self, reason="account_safety", evidence=True, run="one"):
        return s.stop_run(self.root, run, reason, self.now,
                          self.screenshot() if evidence else None, self.notify)

    def resolve(self, run="one", command="resume-stop"):
        return s.resume_stop(self.root, run, self.now + timedelta(minutes=1),
                             {"operator": "synthetic operator", "note": "Reviewed synthetic page"}, command)

    def test_clear_guard_permits_seen_capture_preflight(self):
        for command in ("seen", "capture", "preflight"):
            self.assertIsNone(s.guard(self.root, "one", self.now, command))
        self.assertFalse((self.root / s.SENTINEL).exists())

    def test_stop_refuses_each_new_work_command_and_records_once(self):
        (self.root / s.STOP).touch()
        for command in ("seen", "capture", "preflight"):
            with self.assertRaises(s.SafetyBlocked) as raised:
                s.guard(self.root, "one", self.now, command)
            self.assertEqual(raised.exception.stop_reason, "manual_stop")
        record = self.read("applications/runs/one/safety.json")
        self.assertEqual(record["command"], "seen")
        self.assertEqual(record["stopped_at"], self.now.isoformat())
        before = (self.root / "applications/runs/one/safety.json").read_bytes()
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "one", self.now + timedelta(hours=1), "preflight")
        self.assertEqual(before, (self.root / "applications/runs/one/safety.json").read_bytes())
        self.assertEqual(len(list((self.root / "applications/safety/stops").glob("*.json"))), 1)

    def test_removing_stop_file_never_resumes_a_durable_stop(self):
        (self.root / s.STOP).touch()
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "one", self.now, "seen")
        (self.root / s.STOP).unlink()
        s2 = importlib.reload(s)
        for run in ("one", "a-new-run"):
            with self.assertRaises(s2.SafetyBlocked):
                s2.guard(self.root, run, self.now, "capture")
        self.assertTrue((self.root / s.SENTINEL).is_file())
        self.assertEqual(self.read("applications/runs/a-new-run/safety.json")["stop_reason"], "manual_stop")

    def test_shell_pending_marker_records_stop_even_after_stop_removal(self):
        # The shell can create this marker using ':' redirection, without Python.
        (self.root / s.PENDING).touch()
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "one", self.now, "capture")
        self.assertEqual(self.read("applications/runs/one/safety.json")["stop_reason"], "manual_stop")
        self.resolve()
        self.assertFalse((self.root / s.PENDING).exists())
        self.assertIsNone(s.guard(self.root, "next", self.now, "preflight"))

    def test_account_stop_blocks_other_runs_and_retains_first_reason_timestamp(self):
        record = self.stop()
        self.assertEqual(record["stop_reason"], "account_safety")
        for run in ("one", "other"):
            for command in ("seen", "capture", "preflight"):
                with self.assertRaises(s.SafetyBlocked):
                    s.guard(self.root, run, self.now, command)
        later = s.stop_run(self.root, "one", "manual_stop", self.now + timedelta(hours=1), notify=self.notify)
        self.assertEqual(record, later)

    def test_one_notification_across_repeated_stops_and_restart(self):
        first = self.stop()
        for _ in range(3):
            self.stop()
        s2 = importlib.reload(s)
        s2.stop_run(self.root, "other", "account_safety", self.now, self.screenshot(), self.notify)
        s2.notify_stop(self.root, self.now, self.notify)
        self.notify.assert_called_once_with({key: first[key] for key in ("stop_id", "run_id", "stop_reason")})
        persisted = self.read(f"applications/safety/stops/{first['stop_id']}.json")
        self.assertEqual(persisted["notification"]["status"], "delivered")

    def test_real_process_restart_stays_blocked_without_notification_replay(self):
        first = self.stop()
        result = subprocess.run(
            [sys.executable, str(Path(s.__file__)), "--workspace", str(self.root),
             "--run-id", "restarted", "--notify-local", "guard", "preflight"],
            capture_output=True, text=True, env={"PATH": "/nonexistent"}, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("BLOCKED: account_safety", result.stdout)
        self.assertEqual(self.read(f"applications/safety/stops/{first['stop_id']}.json"), first)
        self.assertTrue((self.root / "applications/runs/restarted/safety.json").exists())
        self.notify.assert_called_once()

    def test_no_implicit_notification_and_pending_delivery_is_explicit(self):
        with patch.object(s.subprocess, "run") as run:
            record = s.stop_run(self.root, "one", "manual_stop", self.now)
            with self.assertRaises(s.SafetyBlocked):
                s.guard(self.root, "one", self.now, "preflight")
            run.assert_not_called()
        self.assertEqual(record["notification"]["status"], "pending")
        delivered = s.notify_stop(self.root, self.now, self.notify)
        self.assertEqual(delivered["notification"]["status"], "delivered")
        self.notify.assert_called_once()

    def test_notification_failure_is_durable_and_not_retried(self):
        self.notify.side_effect = RuntimeError("private provider error")
        first = self.stop()
        self.assertEqual(first["notification"]["status"], "failed")
        self.assertEqual(first["notification"]["error_type"], "RuntimeError")
        self.stop()
        self.notify.assert_called_once()
        self.assertNotIn("private provider error", json.dumps(first))

    def test_interrupted_notification_attempt_never_replays(self):
        self.notify.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.stop()
        result = self.stop()
        self.assertEqual(result["notification"]["status"], "attempting")
        self.notify.assert_called_once()

    def test_screenshot_missing_or_invalid_still_persists_block(self):
        for evidence in (None, {"screenshot": "applications/missing.png"},
                         {"screenshot": "docs/public.png"}, {"contents": "not a reference"}):
            with self.subTest(evidence=evidence):
                with self.assertRaises(s.SafetyError):
                    s.stop_run(self.root, "one", "account_safety", self.now, evidence)
                self.assertTrue((self.root / s.SENTINEL).exists())
                with self.assertRaises(s.SafetyBlocked):
                    s.guard(self.root, "other", self.now, "preflight")
        repaired = self.stop()
        self.assertEqual(repaired["evidence"], self.screenshot())
        self.assertEqual(repaired["stop_reason"], "account_safety")

    def test_missing_screenshot_notifies_and_raises_typed_blocker(self):
        with self.assertRaises(s.EvidenceRequired) as raised:
            self.stop(evidence=False)
        self.notify.assert_called_once()
        self.assertIsNone(raised.exception.record["evidence"])
        self.assertEqual(raised.exception.stop_reason, "account_safety")
        self.assertIn("browser screenshot", str(raised.exception))

    def test_invalid_screenshot_does_not_suppress_stop_notification(self):
        with self.assertRaises(s.EvidenceRequired):
            s.stop_run(self.root, "one", "account_safety", self.now,
                       {"screenshot": "applications/not-captured.png"}, self.notify)
        self.notify.assert_called_once()
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "next", self.now, "preflight")

    def test_private_refs_only_and_public_outputs_do_not_leak(self):
        evidence = self.screenshot()
        evidence.update(page_text="PRIVATE PAGE", url="https://example.test/login?token=SECRET", image_bytes="PRIVATE IMAGE")
        record = s.stop_run(self.root, "one", "account_safety", self.now, evidence, self.notify)
        encoded = json.dumps(record)
        for private in ("PRIVATE", "SECRET", str(self.root)):
            self.assertNotIn(private, encoded)
        self.assertNotIn("screenshot", json.dumps(self.notify.call_args.args[0]))
        self.assertNotIn("screenshot", json.dumps(self.read("applications/runs/one/safety.json")))
        out = io.StringIO()
        with redirect_stdout(out):
            result = s.main(["--workspace", str(self.root), "--run-id", "one", "guard", "capture"])
        self.assertEqual(result, 2)
        self.assertNotIn("synthetic.png", out.getvalue())
        self.assertNotIn(str(self.root), out.getvalue())

    def test_resume_requires_explicit_command_evidence_and_originating_run(self):
        first = self.stop()
        for command, evidence, run in (("capture", {"operator": "test", "note": "reviewed"}, "one"),
                                       ("resume-stop", None, "one"),
                                       ("reconcile", {"operator": "test", "note": ""}, "one"),
                                       ("resume-stop", {"operator": "test", "note": "reviewed"}, "other")):
            with self.subTest(command=command, evidence=evidence, run=run):
                with self.assertRaises(s.SafetyError):
                    s.resume_stop(self.root, run, self.now, evidence, command)
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "one", self.now, "resume-stop")
        resolved = self.resolve()
        self.assertEqual(resolved["status"], "resolved")
        self.assertIsNone(s.guard(self.root, "next", self.now, "preflight"))
        self.assertEqual(self.read("applications/runs/one/safety.json")["stop_reason"], "account_safety")
        self.assertEqual(resolved["stop_id"], first["stop_id"])

    def test_stop_file_prevents_even_explicit_resume(self):
        self.stop()
        (self.root / s.STOP).touch()
        with self.assertRaises(s.SafetyError):
            self.resolve()
        self.assertTrue((self.root / s.SENTINEL).exists())

    def test_new_incident_after_resolution_gets_one_new_notification(self):
        first = self.stop()
        self.resolve(command="reconcile")
        second = self.stop(run="second")
        self.assertNotEqual(first["stop_id"], second["stop_id"])
        self.assertEqual(self.notify.call_count, 2)

    def test_ledger_restores_removed_or_not_yet_written_sentinel(self):
        self.stop()
        (self.root / s.SENTINEL).unlink()
        with self.assertRaises(s.SafetyBlocked):
            s.guard(self.root, "other", self.now, "capture")
        self.assertTrue((self.root / s.SENTINEL).exists())

    def test_corrupt_state_fails_closed(self):
        first = self.stop()
        path = self.root / f"applications/safety/stops/{first['stop_id']}.json"
        path.write_text("not json")
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "other", self.now, "preflight")

    def test_corrupt_sentinel_and_schema_fail_closed(self):
        (self.root / s.SENTINEL).write_text("{}")
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "one", self.now, "seen")
        (self.root / s.SENTINEL).unlink()
        first = self.stop()
        path = self.root / f"applications/safety/stops/{first['stop_id']}.json"
        first["status"] = "unknown"
        path.write_text(json.dumps(first))
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "one", self.now, "preflight")

    def test_resolved_flag_without_explicit_resolution_is_corrupt(self):
        first = self.stop()
        first["status"] = "resolved"
        path = self.root / f"applications/safety/stops/{first['stop_id']}.json"
        path.write_text(json.dumps(first))
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "other", self.now, "preflight")
        self.assertTrue((self.root / s.SENTINEL).exists())

    def test_detector_to_screenshot_to_whole_run_stop(self):
        detection = s.detect("Synthetic page: verify you are human")
        evidence = self.screenshot()
        evidence["kind"] = detection.kind
        record = s.stop_run(self.root, "one", detection.stop_reason, self.now, evidence)
        self.assertEqual(record["evidence"]["kind"], "challenge")
        self.assertEqual(self.read("applications/runs/one/safety.json")["status"], "stopped")
        for command in ("seen", "capture", "preflight"):
            with self.assertRaises(s.SafetyBlocked):
                s.guard(self.root, "next", self.now, command)

    def test_run_settings_snapshot_is_untouched(self):
        path = self.root / "applications/runs/one/run.json"
        path.parent.mkdir(parents=True)
        original = b'{"synthetic immutable run":true}\n'
        path.write_bytes(original)
        self.stop()
        self.resolve()
        self.assertEqual(path.read_bytes(), original)

    def test_unsafe_paths_naive_times_and_command_arguments_rejected(self):
        for run in ("../escape", "", "/absolute"):
            with self.assertRaises(s.SafetyError):
                s.guard(self.root, run, self.now, "seen")
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "one", self.now.replace(tzinfo=None), "seen")
        with self.assertRaises(s.SafetyError):
            s.guard(self.root, "one", self.now, "capture --private-argument")
        with self.assertRaises(s.SafetyError):
            s.stop_run(self.root, "one", "invented", self.now)

    def test_workspace_symlink_and_screenshot_escape_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            (self.root / "applications/safety").symlink_to(other, target_is_directory=True)
            with self.assertRaises(s.SafetyError):
                s.guard(self.root, "one", self.now, "seen")
            self.assertEqual(list(Path(other).iterdir()), [])
            (self.root / "applications/safety").unlink()
            external = Path(other) / "synthetic.png"
            external.write_bytes(b"synthetic external fixture")
            (self.root / "applications/escape.png").symlink_to(external)
            with self.assertRaises(s.SafetyError):
                s.stop_run(self.root, "one", "account_safety", self.now,
                           {"screenshot": "applications/escape.png"})
            with self.assertRaises(s.SafetyBlocked):
                s.guard(self.root, "one", self.now, "preflight")

    def test_workspace_relocation_keeps_relative_evidence(self):
        self.stop()
        moved = self.root / "relocated"
        moved.mkdir()
        (self.root / "applications").rename(moved / "applications")
        with self.assertRaises(s.SafetyBlocked):
            s.guard(moved, "next", self.now, "seen")

    def test_explicit_local_notification_uses_subprocess_without_shell(self):
        with patch.object(s.subprocess, "run") as run:
            record = s.stop_run(self.root, "one", "manual_stop", self.now, notify=s.local_notification)
        self.assertEqual(record["notification"]["status"], "delivered")
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0][0:2], ["osascript", "-e"])
        self.assertNotIn("shell", kwargs)
        self.assertNotIn(str(self.root), str(args))

    def test_cli_notification_is_opt_in_and_stopped_commands_exit_two(self):
        out = io.StringIO()
        (self.root / s.STOP).touch()
        args = ["--workspace", str(self.root), "--run-id", "one"]
        with patch.object(s.subprocess, "run") as run, redirect_stdout(out):
            self.assertEqual(s.main([*args, "guard", "seen"]), 2)
            run.assert_not_called()
            self.assertEqual(s.main([*args, "--notify-local", "guard", "capture"]), 2)
            self.assertEqual(s.main([*args, "--notify-local", "guard", "preflight"]), 2)
            run.assert_called_once()


class DetectionTests(unittest.TestCase):
    def test_synthetic_page_fixtures(self):
        fixtures = (
            ("Please solve the CAPTCHA", "", "captcha"),
            ("Verify you're human", "", "challenge"),
            ("Verify you’re human", "", "challenge"),
            ("<h1>Security verification</h1>", "", "challenge"),
            ("We detected unusual activity", "", "unusual_activity"),
            ("Suspicious activity detected", "", "unusual_activity"),
            ("UnusualActivity", "", "unusual_activity"),
            ("Sign in to continue", "", "sign_in"),
            ("Your session has expired", "", "sign_in"),
            ("Too many requests", "", "rate_limit"),
            ("Rate limit exceeded", "", "rate_limit"),
            ("Try again in 30 minutes", "", "rate_limit"),
            ("Your account has been temporarily restricted", "", "restriction"),
            ("Your account is suspended", "", "restriction"),
            ("Access denied", "", "restriction"),
            ("", "https://www.linkedin.com/checkpoint/challenge?secret=PRIVATE", "challenge"),
            ("", "https://www.linkedin.com/uas/login", "sign_in"),
            ("", "https://www.linkedin.com/authwall", "sign_in"),
            ("", "https://example.test/captcha", "captcha"),
            ("", "https://example.test/account-restricted", "restriction"),
        )
        for text, url, kind in fixtures:
            with self.subTest(text=text, url=url):
                result = s.detect(text, url)
                self.assertIsInstance(result, s.Detection)
                self.assertEqual(result.kind, kind)
                self.assertEqual(result.stop_reason, "account_safety")
                self.assertNotIn("PRIVATE", repr(result))

    def test_normal_listing_and_navigation_sign_in_link_do_not_trigger(self):
        for text in ("Engineer at Synthetic Co. Apply now.",
                     "Software Engineer. Home Jobs Sign in Join now.",
                     "Job requires account management and security experience.", ""):
            self.assertIsNone(s.detect(text, "https://example.test/jobs/view/123?redirect=/login"))

    def test_detection_never_notifies_or_writes(self):
        with patch.object(s.subprocess, "run") as run, patch.object(s, "_write") as write:
            self.assertEqual(s.detect("Unusual activity").kind, "unusual_activity")
            run.assert_not_called()
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
