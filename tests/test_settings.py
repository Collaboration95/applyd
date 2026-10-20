"""Settings behavior with independent synthetic workspaces; no live actions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import settings as s


class SettingsTests(unittest.TestCase):
    def test_new_optional_defaults_preserve_original_frozen_settings_bytes(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        saved = s.run_start(self.root, "legacy-snapshot", now)
        saved["settings"].pop("follow_up_days")
        saved["settings_hash"] = hashlib.sha256(json.dumps(saved["settings"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        path = self.root / "applications/runs/legacy-snapshot/run.json"
        path.write_text(json.dumps(saved))
        original = path.read_bytes()
        self.assertEqual(s.load_run(self.root, "legacy-snapshot"), saved)
        self.assertEqual(path.read_bytes(), original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "first"
        self.other = self.base / "second"
        self.root.mkdir()
        self.other.mkdir()
        self.now = datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc)

    def write(self, name, data, root=None):
        path = (root or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def config(self, root=None, **values):
        self.write("settings.json", {"schema_version": 1, **values}, root)
        return s.load_settings(root or self.root)

    def ready(self, **values):
        return {"application_id": "candidate", "status": "ready", "company": "Example Labs",
                "role": "Engineer", "location": "Example City", "platform": "linkedin",
                "listing_url": "https://www.linkedin.com/jobs/view/900", "easy_apply": True, **values}

    def confirmed(self, identity, root=None, **values):
        record = {"application_id": identity, "status": "submitted",
                  "submitted_at": self.now.isoformat(), **values}
        self.write(f"applications/records/{identity}/record.json", record, root)
        return record

    def test_missing_settings_compatible_and_independent(self):
        before = list(self.root.iterdir())
        first = s.load_settings(self.root)
        self.assertEqual(first["mode"], "apply")
        self.assertFalse(first["easy_apply_only"])
        self.assertIsNone(first["daily_cap"])
        self.assertEqual(first["timezone"], "UTC")
        first["external_hosts"]["deny"].append("example.invalid")
        self.assertEqual(s.load_settings(self.root)["external_hosts"]["deny"], [])
        self.assertEqual(list(self.root.iterdir()), before)
        self.assertTrue(s.check_submission(self.root, self.ready(), self.now)["ready"])

    def test_two_workspaces_never_cross_read_even_with_environment(self):
        self.config(mode="save_only", daily_cap=0, browser="example-browser", resume_scope="example")
        self.config(self.other, timezone="Europe/London", role_rules={"locations": ["Example City"]})
        self.confirmed("one")
        with patch.dict("os.environ", {"APPLYD_WORKSPACE": str(self.root)}):
            result = s.check_submission(self.other, self.ready(), self.now)
            self.assertEqual(result["daily_count"], 0)
            self.assertEqual(s.load_settings(self.other)["timezone"], "Europe/London")
            with self.assertRaisesRegex(s.SettingsError, "mode:save_only"):
                s.check_submission(self.root, self.ready(), self.now)

    def test_missing_workspace_and_malformed_json_actionable(self):
        with self.assertRaisesRegex(s.SettingsError, "initialize"):
            s.load_settings(self.base / "missing")
        path = self.root / "settings.json"
        for value in ("{", '{"mode":"apply","mode":"dry_run"}', '{"daily_cap":NaN}', "[]"):
            with self.subTest(value=value):
                path.write_text(value)
                with self.assertRaises(s.SettingsError):
                    s.load_settings(self.root)

    def test_config_types_unknown_keys_regex_hosts_timezone(self):
        invalid = [{"schema_version": 2}, {"schema_version": True}, {"mode": "unknown"},
                   {"easy_apply_only": 1}, {"daily_cap": True}, {"per_run_cap": -1},
                   {"per_run_minutes": 1.5}, {"max_required_years": "3"},
                   {"company_blocklist": "Example"}, {"title_blocklist": ["["]},
                   {"external_hosts": []}, {"external_hosts": {"permit": []}},
                   {"external_hosts": {"allow": ["https://example.invalid"]}},
                   {"external_hosts": {"deny": ["*.example.invalid"]}},
                   {"retryable_statuses": ["submitted"]}, {"retryable_statuses": ["submission_uncertain"]},
                   {"start_mode": "https://evil.invalid/jobs/search"}, {"timezone": "Mars/Example"},
                   {"timezone": None}, {"browser": []}, {"resume_scope": ""},
                   {"role_rules": []}, {"daily_limit_typo": 3}]
        for value in invalid:
            with self.subTest(value=value):
                self.write("settings.json", value)
                with self.assertRaisesRegex(s.SettingsError, "settings.json"):
                    s.load_settings(self.root)

    def test_example_is_valid_and_contains_all_defaults(self):
        example = Path(__file__).resolve().parents[1] / "settings.example.json"
        raw = json.loads(example.read_text())
        self.assertEqual(set(raw), set(s.DEFAULTS))
        self.write("settings.json", raw)
        self.assertEqual(s.load_settings(self.root), raw)

    def test_hash_canonical_and_effective_defaults(self):
        settings = self.config(company_blocklist=["Example"], role_rules={"b": 2, "a": 1})
        reordered = dict(reversed(list(settings.items())))
        expected = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(s.settings_hash(settings), expected)
        self.assertEqual(s.settings_hash(reordered), expected)
        reordered["mode"] = "dry_run"
        self.assertNotEqual(s.settings_hash(reordered), expected)
        self.assertEqual(s.settings_hash({}), s.settings_hash(s.DEFAULTS))

    def test_run_exact_snapshot_hash_deadline_and_immutable_id(self):
        settings = self.config(timezone="Asia/Tokyo", per_run_minutes=10, browser="example")
        run = s.run_start(self.root, "run-1", self.now)
        self.assertEqual(run["settings"], settings)
        self.assertEqual(run["settings_hash"], s.settings_hash(settings))
        self.assertEqual(run["started_at"], "2026-01-03T01:30:00+09:00")
        self.assertEqual(run["deadline"], "2026-01-03T01:40:00+09:00")
        path = self.root / "applications/runs/run-1/run.json"
        original = path.read_bytes()
        self.config(mode="dry_run")
        with self.assertRaisesRegex(s.SettingsError, "already exists"):
            s.run_start(self.root, "run-1", self.now)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(s.load_run(self.root, "run-1"), run)

    def test_concurrent_run_start_has_one_complete_winner(self):
        def start(_):
            try:
                return s.run_start(self.root, "same-run", self.now)
            except s.SettingsError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertIn(s.load_run(self.root, "same-run"), results)
        self.assertEqual(list((self.root / "applications/runs/same-run").glob(".run-*")), [])

    def test_relocation_preserves_settings_run_and_relative_artifacts(self):
        self.config(per_run_cap=2, per_run_minutes=30)
        run = s.run_start(self.root, "relocatable", self.now)
        self.confirmed("one", run_id="relocatable")
        shutil.copytree(self.root, self.base / "relocated")
        shutil.rmtree(self.root)
        moved = self.base / "relocated"
        self.assertEqual(s.load_run(moved, "relocatable"), run)
        result = s.check_submission(moved, self.ready(run_id="relocatable"), self.now)
        self.assertEqual((result["daily_count"], result["run_count"]), (1, 1))
        self.assertNotIn(str(self.base), json.dumps(run))

    def test_run_tampering_or_missing_snapshot_fails_closed(self):
        run = s.run_start(self.root, "example", self.now)
        for key, value in (("settings_hash", "0" * 64), ("deadline", self.now.isoformat()),
                           ("run_id", "wrong"), ("settings", {"mode": "apply"})):
            with self.subTest(key=key):
                self.write("applications/runs/example/run.json", {**run, key: value})
                with self.assertRaises(s.SettingsError):
                    s.load_run(self.root, "example")
        with self.assertRaisesRegex(s.SettingsError, "start the run"):
            s.load_run(self.root, "missing")

    def test_unsafe_run_ids_and_cross_workspace_symlinks_rejected(self):
        for run_id in ("../escape", "/absolute", "", ".", "a/b"):
            with self.subTest(run_id=run_id), self.assertRaises(s.SettingsError):
                s.run_start(self.root, run_id, self.now)
        self.config(self.other, mode="dry_run")
        (self.root / "settings.json").symlink_to(self.other / "settings.json")
        with self.assertRaisesRegex(s.SettingsError, "escapes"):
            s.load_settings(self.root)

    def test_modes_refuse_submission(self):
        for mode in ("save_only", "dry_run"):
            with self.subTest(mode=mode):
                self.config(mode=mode)
                with self.assertRaisesRegex(s.SettingsError, f"mode:{mode}"):
                    s.check_submission(self.root, self.ready(), self.now)

    def test_external_and_blocklisted_headers_have_stable_precedence(self):
        settings = self.config(easy_apply_only=True, company_blocklist=["example"], title_blocklist=[r"^Senior\b"])
        self.assertEqual(s.eligibility(self.ready(easy_apply=False), settings), "skipped:external_apply")
        self.assertEqual(s.eligibility(self.ready(), settings), "skipped:blocklist")
        self.assertEqual(s.eligibility({"company": "Other", "title": "Senior Engineer"}, settings), "skipped:blocklist")
        self.assertIsNone(s.eligibility({"company": "Other", "title": "Engineer"}, settings))
        self.assertEqual(s.eligibility({"application_url": "https://example.invalid/role"}, settings), "skipped:external_apply")
        self.assertEqual(s.eligibility({"is_easy_apply": False}, settings), "skipped:external_apply")

    def test_external_host_allow_deny_subdomains_and_spoofing(self):
        settings = self.config(external_hosts={"allow": ["EXAMPLE.invalid"], "deny": ["blocked.example.invalid"]})
        for host, result in (("jobs.example.invalid", None), ("blocked.example.invalid", "skipped:external_host"),
                             ("a.blocked.example.invalid", "skipped:external_host"),
                             ("example.invalid.evil.invalid", "skipped:external_host"),
                             ("notexample.invalid", "skipped:external_host"), ("www.linkedin.com", None)):
            with self.subTest(host=host):
                self.assertEqual(s.eligibility({"application_url": f"https://{host}/role"}, settings), result)
        for url in ("javascript:alert(1)", "https://example.invalid@evil.invalid/", "//example.invalid/job",
                    "https://example.invalid:bad/job", "https://example.invalid\\@evil.invalid/job"):
            with self.subTest(url=url):
                self.assertEqual(s.eligibility({"application_url": url}, settings), "skipped:invalid_application_url")

    def test_max_required_years_only_uses_explicit_extracted_fact(self):
        settings = self.config(max_required_years=3)
        self.assertIsNone(s.eligibility({"required_years": 3}, settings))
        self.assertEqual(s.eligibility({"required_years": 4}, settings), "skipped:required_years")
        self.assertIsNone(s.eligibility({"title": "4 years preferred"}, settings))
        for value in (True, "3+", -1, float("nan")):
            with self.subTest(value=value), self.assertRaises(s.SettingsError):
                s.eligibility({"required_years": value}, settings)

    def test_daily_counts_unique_confirmed_and_survive_followups(self):
        self.config(daily_cap=2)
        self.confirmed("one", platform="linkedin", platform_job_id="1", status="interview")
        self.confirmed("duplicate", platform="linkedin", platform_job_id="1")
        self.confirmed("old", submitted_at=(self.now - timedelta(days=1)).isoformat())
        self.write("applications/records/uncertain/record.json", {"application_id": "uncertain", "status": "submission_uncertain"})
        result = s.check_submission(self.root, self.ready(), self.now)
        self.assertEqual(result["daily_count"], 1)
        self.confirmed("two")
        with self.assertRaisesRegex(s.SettingsError, "cap:daily"):
            s.check_submission(self.root, self.ready(), self.now)

    def test_dedupe_requisition_canonical_url_and_cross_post_bridge(self):
        self.config(daily_cap=2)
        self.confirmed("one", company="Example", employer_requisition_id="req-1")
        self.confirmed("two", company="example", employer_requisition_id="req-1",
                       listing_url="https://example.invalid/jobs/1?utm_source=synthetic")
        self.confirmed("three", listing_url="https://example.invalid/jobs/1")
        self.confirmed("four", listing_url="https://example.invalid/jobs/1", company="Example",
                       role="Engineer", location="City")
        self.confirmed("five", company="example", role="engineer", location="city")
        self.assertEqual(s.check_submission(self.root, self.ready(), self.now)["daily_count"], 1)

    def test_daily_timezone_boundary_and_frozen_timezone(self):
        self.config(timezone="Asia/Tokyo", daily_cap=1)
        self.confirmed("one", submitted_at="2026-01-02T16:00:00+00:00")
        run = s.run_start(self.root, "zone", self.now)
        # In UTC the later candidate is on a new day; in Tokyo it is the same day.
        self.config(timezone="UTC", daily_cap=1)
        later = datetime(2026, 1, 3, 0, 1, tzinfo=timezone.utc)
        self.assertTrue(s.check_submission(self.root, self.ready(), later)["ready"])
        with self.assertRaisesRegex(s.SettingsError, "cap:daily"):
            s.check_submission(self.root, self.ready(run_id=run["run_id"]), later)

    def test_run_caps_from_records_and_prepared_run_id(self):
        self.config(per_run_cap=1)
        s.run_start(self.root, "active", self.now)
        candidate = self.ready(prepared={"run_id": "active"})
        self.confirmed("other-run", run_id="other")
        self.assertEqual(s.check_submission(self.root, candidate, self.now)["run_count"], 0)
        self.confirmed("same-run", run_id="active")
        with self.assertRaisesRegex(s.SettingsError, "cap:per_run"):
            s.check_submission(self.root, candidate, self.now)

    def test_submission_history_keeps_original_run_after_reassignment(self):
        self.config(per_run_cap=1)
        s.run_start(self.root, "active", self.now)
        self.confirmed("one", run_id="active", status="rejected", submissions=[{
            "status": "submitted", "submitted_at": self.now.isoformat(), "run_id": "original"}])
        self.assertEqual(s.check_submission(self.root, self.ready(run_id="active"), self.now)["run_count"], 0)

    def test_deadline_boundary_tightening_and_elapsed_dst_minutes(self):
        self.config(per_run_minutes=10)
        s.run_start(self.root, "timed", self.now)
        candidate = self.ready(run_id="timed")
        self.assertTrue(s.check_submission(self.root, candidate, self.now + timedelta(minutes=9))["ready"])
        self.config(per_run_minutes=60)
        with self.assertRaisesRegex(s.SettingsError, "cap:deadline"):
            s.check_submission(self.root, candidate, self.now + timedelta(minutes=10))
        self.config(per_run_minutes=5)
        with self.assertRaisesRegex(s.SettingsError, "cap:deadline"):
            s.check_submission(self.root, candidate, self.now + timedelta(minutes=5))
        self.config(timezone="America/New_York", per_run_minutes=60)
        spring = datetime(2026, 3, 8, 1, 30, tzinfo=ZoneInfo("America/New_York"))
        run = s.run_start(self.root, "dst", spring)
        self.assertEqual(run["deadline"], "2026-03-08T03:30:00-04:00")

    def test_current_settings_cannot_weaken_frozen_restrictions(self):
        cases = [({"mode": "dry_run"}, {}, {}, "mode:dry_run"),
                 ({"easy_apply_only": True}, {}, {"easy_apply": False}, "skipped:external_apply"),
                 ({"company_blocklist": ["Example"]}, {}, {}, "skipped:blocklist"),
                 ({"daily_cap": 0}, {"daily_cap": 10}, {}, "cap:daily"),
                 ({"per_run_cap": 0}, {"per_run_cap": 10}, {}, "cap:per_run"),
                 ({"max_required_years": 2}, {}, {"required_years": 3}, "skipped:required_years"),
                 ({"external_hosts": {"deny": ["example.invalid"]}}, {},
                  {"application_url": "https://example.invalid/job"}, "skipped:external_host")]
        for i, (frozen, current, header, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                self.config(**frozen)
                run_id = f"frozen-{i}"
                s.run_start(self.root, run_id, self.now)
                self.config(**current)
                with self.assertRaisesRegex(s.SettingsError, reason):
                    s.check_submission(self.root, self.ready(run_id=run_id, **header), self.now)

    def test_current_restrictions_tighten_existing_run(self):
        s.run_start(self.root, "unlimited", self.now)
        for current, reason in (({"mode": "save_only"}, "mode:save_only"),
                                ({"company_blocklist": ["Example"]}, "skipped:blocklist"),
                                ({"daily_cap": 0}, "cap:daily")):
            with self.subTest(current=current):
                self.config(**current)
                with self.assertRaisesRegex(s.SettingsError, reason):
                    s.check_submission(self.root, self.ready(run_id="unlimited"), self.now)

    def test_missing_run_caps_zero_naive_clock_and_nonready(self):
        self.config(per_run_cap=3)
        with self.assertRaisesRegex(s.SettingsError, "run:required"):
            s.check_submission(self.root, self.ready(), self.now)
        self.config(per_run_minutes=0)
        s.run_start(self.root, "zero", self.now)
        with self.assertRaisesRegex(s.SettingsError, "cap:deadline"):
            s.check_submission(self.root, self.ready(run_id="zero"), self.now)
        with self.assertRaisesRegex(s.SettingsError, "precedes"):
            s.check_submission(self.root, self.ready(run_id="zero"), self.now - timedelta(seconds=1))
        with self.assertRaisesRegex(s.SettingsError, "timezone-aware"):
            s.run_start(self.root, "naive", self.now.replace(tzinfo=None))
        with self.assertRaisesRegex(s.SettingsError, "ready"):
            s.check_submission(self.root, self.ready(status="submitted"), self.now)
        with self.assertRaisesRegex(s.SettingsError, "disagrees"):
            s.check_submission(self.root, self.ready(run_id="zero", prepared={"run_id": "other"}), self.now)

    def test_corrupt_record_cannot_silently_disable_caps(self):
        self.config(daily_cap=1)
        self.confirmed("one", submitted_at="not-a-date")
        with self.assertRaisesRegex(s.SettingsError, "ISO timestamp"):
            s.check_submission(self.root, self.ready(), self.now)


if __name__ == "__main__":
    unittest.main()
