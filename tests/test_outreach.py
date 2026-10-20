"""Synthetic records and fake adapters only; no workspace or account access."""

import ast
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import outreach


NOW = "2026-10-20T10:00:00+08:00"
SUBMITTED = "2026-10-05T10:00:00+08:00"
POINT = {"project_id": "example-api", "bullet_id": "api-1",
         "text": "Built a Python API.", "catalog_fingerprint": "a" * 64}
POLICY = {"enabled": True, "business_days": 10, "daily_draft_cap": 3, "do_not_contact": []}


def application(app_id="app-a", **changes):
    result = {"application_id": app_id, "company": "Example Company", "role": "Developer",
              "location": "Singapore", "platform": "linkedin", "platform_job_id": "101",
              "listing_url": "https://www.linkedin.com/jobs/view/101", "status": "submitted",
              "submitted_at": SUBMITTED, "events": [{"status": "submitted", "at": SUBMITTED}],
              "contacts": [{"contact_id": "contact-fictional", "name": "Example Recruiter",
                            "headline": "Hiring", "source": "jd_text", "verified": False,
                            "captured_at": SUBMITTED, "email": "recruiter@example.invalid",
                            "profile_url": "https://www.linkedin.com/in/example-fictional",
                            "relationship": "unknown"}],
              "submissions": [{"id": "resume-example", "sha256": "b" * 64,
                               "source_master_id": "master-example",
                               "exact_resume_copy": "applications/records/example/resume.pdf",
                               "submitted_at": SUBMITTED,
                               "confirmation_path": "applications/records/example/confirmation.txt",
                               "confirmation_sha256": "c" * 64,
                               "selected_projects": [{"id": POINT["project_id"],
                                                      "content_hash": POINT["catalog_fingerprint"],
                                                      "bullet_ids": [POINT["bullet_id"]]}]}]}
    result.update(changes)
    return result


def independent(app_id):
    return application(app_id, company="Example " + app_id,
                       listing_url="https://jobs.example.invalid/" + app_id,
                       platform="example", platform_job_id=app_id)


class FakeAdapter:
    def __init__(self, store, result="actual-fake-draft-id", error=None):
        self.store, self.result, self.error = store, result, error
        self.calls = []

    def create_draft(self, **kwargs):
        # Read actual persisted bytes, not the caller's in-memory dictionaries.
        events = [e for r in self.store.load() for e in r.get("outreach", [])]
        reserved = next(e for e in events if e["follow_up_id"] == kwargs["reservation_id"])
        if reserved["status"] != "draft_uncertain":
            raise AssertionError("No durable reservation before adapter call")
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class Store:
    def __init__(self, root, records):
        self.path = root / "synthetic-records.json"
        self.path.write_text(json.dumps(records))
        self.writes = 0
        self.fail_write = None

    def load(self):
        return json.loads(self.path.read_text())

    def persist(self, record):
        self.writes += 1
        if self.writes == self.fail_write:
            raise OSError("synthetic persistence failure")
        records = self.load()
        records = [record if r["application_id"] == record["application_id"] else r for r in records]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records))
        temporary.replace(self.path)


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name), [application()])
        self.adapter = FakeAdapter(self.store)

    def create(self, app_id="app-a", **changes):
        args = {"load_records": self.store.load, "persist_record": self.store.persist,
                "policy": POLICY, "now": NOW, "contact_id": "contact-fictional",
                "channel": "email", "trusted_point": POINT, "adapter": self.adapter}
        args.update(changes)
        return outreach.create_follow_up(app_id, **args)

    def edit(self, change, app_id="app-a"):
        record = next(r for r in self.store.load() if r["application_id"] == app_id)
        change(record)
        self.store.persist(record)

    def test_default_off_and_strict_policy(self):
        self.assertEqual(self.create(policy=None)["reason"], "disabled")
        self.assertEqual(self.store.writes, 0)
        for bad in ({"enabled": 1}, {"daily_draft_cap": True}, {"business_days": -1},
                    {"business_days": 1.5}, {"do_not_contact": [""]}, {"extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(outreach.OutreachError):
                outreach.validate_policy(bad)
        self.assertFalse(outreach.DEFAULT_POLICY["enabled"])

    def test_business_day_boundaries_weekends_and_offsets(self):
        cases = [("2026-10-09T10:00:00+08:00", 1, "2026-10-12T10:00:00+08:00"),
                 ("2026-10-10T10:00:00+08:00", 1, "2026-10-12T10:00:00+08:00"),
                 ("2026-10-11T10:00:00+08:00", 5, "2026-10-16T10:00:00+08:00"),
                 (SUBMITTED, 10, "2026-10-19T10:00:00+08:00"),
                 ("2026-10-09T02:00:00Z", 1, "2026-10-12T10:00:00+08:00"),
                 ("2026-10-10T10:00:00+08:00", 0, "2026-10-10T10:00:00+08:00")]
        for start, days, expected in cases:
            with self.subTest(start=start, days=days):
                self.assertEqual(outreach.business_day_due(start, days).isoformat(), expected)
        self.assertEqual(self.create(now="2026-10-19T09:59:59+08:00")["reason"], "not_due")
        self.assertEqual(self.create(now="2026-10-19T10:00:00+08:00")["status"], "draft_created")

    def test_business_day_arithmetic_matches_slow_reference(self):
        start = datetime.fromisoformat("2026-10-05T10:00:00+08:00")
        for offset in range(7):
            for days in range(32):
                due = start + timedelta(days=offset)
                left = days
                while left:
                    due += timedelta(days=1)
                    if due.weekday() < 5:
                        left -= 1
                self.assertEqual(outreach.business_day_due(start + timedelta(days=offset), days), due)

    def test_success_reserves_before_only_adapter_call_and_stores_sources(self):
        before = deepcopy(self.store.load()[0])
        result = self.create()
        after = self.store.load()[0]
        event = after["outreach"][0]
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["daily_count"], 1)
        self.assertEqual(event["adapter_id"], "actual-fake-draft-id")
        self.assertEqual(event["sources"]["project"], POINT)
        self.assertEqual(event["sources"]["contact"], before["contacts"][0])
        self.assertEqual(event["sources"]["resume_sha256"], "b" * 64)
        self.assertEqual(event["sources"]["confirmation_sha256"], "c" * 64)
        self.assertEqual(self.adapter.calls[0]["body"], event["text"])
        for fragment in (before["role"], POINT["text"], "Thanks"):
            self.assertIn(fragment, event["text"])
        for key, value in before.items():
            self.assertEqual(after[key], value)
        self.assertEqual(set(result), {"application_id", "status", "reason", "daily_count", "follow_up_id"})
        self.assertNotIn("recruiter@", json.dumps(result))

    def test_fresh_restart_and_channel_switch_cannot_repeat_followup(self):
        first = self.create()
        new_adapter = FakeAdapter(self.store)
        for changes in ({"adapter": new_adapter}, {"channel": "linkedin", "adapter": None},
                        {"now": "2026-10-21T10:00:00+08:00"}):
            result = self.create(**changes)
            self.assertEqual(result["reason"], "already_followed_up")
            self.assertEqual(result["follow_up_id"], first["follow_up_id"])
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(new_adapter.calls, [])

    def test_response_outcome_and_withdrawal_stop_even_if_later_or_alias(self):
        for field, value in (("status", "rejected"), ("outcome", "acknowledged"),
                             ("outcome", "withdrawn"), ("outcome", "interview"),
                             ("outcome_events", [{"status": "offer"}]),
                             ("outcome_events", [{"status": "assessment"}])):
            with self.subTest(field=field, value=value):
                original = application()
                original[field] = value
                self.store.path.write_text(json.dumps([original]))
                self.assertEqual(self.create()["reason"], "response_or_closed")
        self.assertEqual(self.adapter.calls, [])

    def test_submission_uncertainty_cannot_be_used(self):
        self.edit(lambda r: r.update(status="submission_uncertain"))
        self.assertEqual(self.create()["reason"], "submission_uncertain")

    def test_missing_confirmed_submission_evidence_blocks(self):
        for key in ("submitted_at", "confirmation_path", "confirmation_sha256", "sha256", "exact_resume_copy", "id"):
            with self.subTest(key=key):
                original = application()
                original["submissions"][0].pop(key)
                self.store.path.write_text(json.dumps([original]))
                self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        self.store.path.write_text(json.dumps([application(submissions=[])]))
        self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        self.assertEqual(self.adapter.calls, [])

    def test_missing_or_nonmatching_project_provenance_blocks_no_invention(self):
        for point in (None, {}, {**POINT, "text": ""}, {**POINT, "catalog_fingerprint": "d" * 64},
                      {**POINT, "bullet_id": "not-in-cv"}, {**POINT, "project_id": "other-project"},
                      {**POINT, "text": "unsafe\nheader"}, {**POINT, "extra": True}):
            with self.subTest(point=point):
                self.assertEqual(self.create(trusted_point=point)["reason"], "unverifiable_project_source")
        self.edit(lambda r: r["submissions"][0].update(selected_projects=[]))
        self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        self.assertEqual(self.store.load()[0].get("outreach", []), [])

    def test_malformed_saved_provenance_is_a_blocker_not_a_fallback(self):
        for selection in (None, {}, [None], [{"id": POINT["project_id"],
                                             "content_hash": POINT["catalog_fingerprint"], "bullet_ids": None}]):
            with self.subTest(selection=selection):
                self.edit(lambda r: r["submissions"][0].update(selected_projects=selection))
                self.assertEqual(self.create()["reason"], "unverifiable_project_source")
        self.assertEqual(self.adapter.calls, [])

    def test_do_not_contact_contact_company_and_application_alias(self):
        for blocked in ("CONTACT-FICTIONAL", "RECRUITER@EXAMPLE.INVALID", "Example Recruiter",
                        "https://www.linkedin.com/in/example-fictional", "Example Company", "app-a"):
            with self.subTest(blocked=blocked):
                result = self.create(policy={**POLICY, "do_not_contact": [blocked]})
                self.assertEqual(result["reason"], "do_not_contact")
        self.edit(lambda r: r.update(do_not_contact=True))
        self.assertEqual(self.create()["reason"], "do_not_contact")
        self.assertEqual(self.adapter.calls, [])

    def test_missing_contact_channel_target_and_invalid_profile_block(self):
        self.assertEqual(self.create(contact_id="absent")["reason"], "contact_missing")
        self.edit(lambda r: r["contacts"][0].update(email=None))
        self.assertEqual(self.create()["reason"], "email_missing")
        self.edit(lambda r: r["contacts"][0].update(profile_url=None))
        self.assertEqual(self.create(channel="linkedin")["reason"], "profile_missing")
        self.edit(lambda r: r["contacts"][0].update(profile_url="javascript:alert(1)"))
        with self.assertRaises(outreach.OutreachError):
            self.create(channel="linkedin")
        self.assertEqual(self.adapter.calls, [])

    def test_credential_blocker_is_durable_without_fake_draft_or_reservation(self):
        for adapter in (None, object()):
            result = self.create(adapter=adapter)
            self.assertEqual(result["reason"], "credentials_required")
            self.assertEqual(result["daily_count"], 0)
            record = self.store.load()[0]
            self.assertEqual(record["outreach_blocker"]["reason"], "credentials_required")
            self.assertNotIn("outreach", record)
        self.assertEqual(self.create()["status"], "draft_created")
        self.assertNotIn("outreach_blocker", self.store.load()[0])

    def test_adapter_timeout_exception_and_missing_id_remain_uncertain(self):
        for kwargs, reason in (({"error": TimeoutError("private marker")}, "adapter_error"),
                               ({"error": outreach.CredentialBlocker("secret marker")}, "credentials_required"),
                               ({"result": None}, "adapter_id_missing"),
                               ({"result": {}}, "adapter_id_missing"),
                               ({"result": ""}, "adapter_id_missing")):
            with self.subTest(reason=reason, kwargs=kwargs):
                self.store.path.write_text(json.dumps([application()]))
                adapter = FakeAdapter(self.store, **kwargs)
                first = self.create(adapter=adapter)
                self.assertEqual(first["status"], "draft_uncertain")
                self.assertEqual(first["reason"], reason)
                second = self.create(adapter=adapter)
                self.assertEqual(second["reason"], "draft_uncertain")
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(second["daily_count"], 1)
                self.assertNotIn("marker", self.store.path.read_text())

    def test_process_interruption_is_durable_and_never_retried(self):
        interrupted = FakeAdapter(self.store, error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.create(adapter=interrupted)
        self.assertEqual(self.store.load()[0]["outreach"][0]["status"], "draft_uncertain")
        self.assertEqual(self.create()["reason"], "draft_uncertain")
        self.assertEqual(self.adapter.calls, [])

    def test_failed_reservation_does_not_call_adapter(self):
        self.store.fail_write = 1
        with self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.adapter.calls, [])

    def test_failure_after_actual_draft_retains_uncertainty(self):
        self.store.fail_write = 2
        with self.assertRaises(OSError):
            self.create()
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.create()["reason"], "draft_uncertain")
        self.assertEqual(len(self.adapter.calls), 1)

    def test_linkedin_note_is_local_and_counts_with_exact_200_boundary(self):
        base = "Following up on Developer. " + " Thanks!"
        point = {**POINT, "text": "x" * (200 - len(base))}
        result = self.create(channel="linkedin", trusted_point=point, adapter=None)
        event = self.store.load()[0]["outreach"][0]
        self.assertEqual(result["status"], "note_created")
        self.assertEqual(len(event["text"]), 200)
        self.assertEqual(result["daily_count"], 1)
        self.assertEqual(event["profile_url"], application()["contacts"][0]["profile_url"])
        self.assertNotIn("adapter_id", event)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.create()["reason"], "already_followed_up")

    def test_oversize_note_blocks_without_truncating_source(self):
        self.assertEqual(self.create(channel="linkedin", trusted_point={**POINT, "text": "x" * 200})["reason"], "note_too_long")
        self.assertEqual(self.create(channel="linkedin", trusted_point={**POINT, "text": "🙂" * 90})["reason"], "note_too_long")
        self.assertNotIn("outreach", self.store.load()[0])

    def test_uncertainty_keeps_consuming_future_daily_caps_until_reconciled(self):
        self.store.path.write_text(json.dumps([independent("app-a"), independent("app-b")]))
        first = self.create(adapter=FakeAdapter(self.store, error=TimeoutError()))
        later = "2026-10-22T10:00:00+08:00"
        self.assertEqual(self.create("app-b", now=later, policy={**POLICY, "daily_draft_cap": 1})["reason"], "daily_cap")
        self.reconcile(first["follow_up_id"], now=later)
        self.assertEqual(self.create("app-b", now=later, policy={**POLICY, "daily_draft_cap": 1})["status"], "draft_created")

    def test_daily_cap_combines_local_notes_email_and_uncertainty(self):
        self.store.path.write_text(json.dumps([independent("app-" + str(i)) for i in range(5)]))
        self.create("app-0", channel="linkedin")
        self.create("app-1", adapter=FakeAdapter(self.store, error=TimeoutError()))
        self.create("app-2")
        self.assertEqual(self.create("app-3")["reason"], "daily_cap")
        self.assertEqual(self.create("app-3")["daily_count"], 3)
        self.assertEqual(self.create("app-3", now="2026-10-21T00:00:00+08:00")["status"], "draft_created")
        self.assertEqual(self.create("app-1", now="2026-10-21T00:00:00+08:00")["reason"], "draft_uncertain")

    def test_daily_cap_zero_and_local_midnight_with_utc_input(self):
        self.assertEqual(self.create(policy={**POLICY, "daily_draft_cap": 0})["reason"], "daily_cap")
        self.store.path.write_text(json.dumps([independent("app-a"), independent("app-b")]))
        self.create(now="2026-10-20T15:59:59Z")
        self.assertEqual(self.create("app-b", policy={**POLICY, "daily_draft_cap": 1}, now="2026-10-20T16:00:00Z")["daily_count"], 1)

    def test_crossposts_transitive_bridges_and_explicit_aliases_block(self):
        a = application()
        bridge = application("app-b", company="Example Bridge", role="Other", platform_job_id="202",
                             listing_url="https://jobs.example.invalid/bridge", employer_requisition_id="REQ1")
        a["employer_requisition_id"] = "REQ1"
        bridge["company"] = a["company"]
        c = application("app-c", company="Example Third", role="Third", platform_job_id="202",
                        listing_url="https://jobs.example.invalid/third")
        self.store.path.write_text(json.dumps([a, bridge, c]))
        self.create()
        self.assertEqual(self.create("app-c")["reason"], "already_followed_up")
        # Explicit aliases can join records with no natural identity overlap.
        self.store.path.write_text(json.dumps([application(), independent("app-z")]))
        self.create(aliases=[("app-a", "app-z")])
        self.assertEqual(self.create("app-z")["reason"], "already_followed_up")

    def test_identity_boundaries_and_url_tracking_aliases(self):
        a, b = application(), independent("app-b")
        b.update(platform_job_id="unrelated", listing_url="https://www.linkedin.com/jobs/view/synthetic-role-101/?trk=example")
        self.store.path.write_text(json.dumps([a, b]))
        self.create()
        self.assertEqual(self.create("app-b")["reason"], "already_followed_up")
        self.store.path.write_text(json.dumps([a, independent("app-b")]))
        self.create()
        self.assertEqual(self.create("app-b")["status"], "draft_created")

    def test_crosspost_identity_without_saved_listing_url_is_still_deduplicated(self):
        a, b = application(), application("app-b", listing_url=None)
        self.store.path.write_text(json.dumps([a, b]))
        self.create()
        self.assertEqual(self.create("app-b")["reason"], "already_followed_up")

    def test_alias_response_or_do_not_contact_cannot_be_bypassed(self):
        self.store.path.write_text(json.dumps([application(), application("app-b", outcome="assessment")]))
        self.assertEqual(self.create()["reason"], "response_or_closed")
        self.store.path.write_text(json.dumps([application(), application("app-b")]))
        self.assertEqual(self.create(policy={**POLICY, "do_not_contact": ["app-b"]})["reason"], "do_not_contact")

    def reconcile(self, follow_up_id, **changes):
        args = {"load_records": self.store.load, "persist_record": self.store.persist,
                "now": NOW, "resolution": "not_created", "evidence": "private/example-check.txt",
                "note": "User checked drafts and confirmed absence."}
        args.update(changes)
        return outreach.reconcile_follow_up("app-a", follow_up_id, **args)

    def test_reconciliation_requires_evidence_and_actual_id_then_never_retries(self):
        result = self.create(adapter=FakeAdapter(self.store, error=TimeoutError()))
        fid = result["follow_up_id"]
        for changes in ({"evidence": ""}, {"note": ""}, {"resolution": "draft_created"},
                        {"resolution": "note_created"}):
            with self.subTest(changes=changes), self.assertRaises(outreach.OutreachError):
                self.reconcile(fid, **changes)
        done = self.reconcile(fid, resolution="draft_created", adapter_id="observed-fake-id")
        self.assertEqual(done["status"], "draft_created")
        self.assertEqual(self.reconcile(fid, resolution="draft_created", adapter_id="observed-fake-id"), done)
        self.assertEqual(self.create()["reason"], "already_followed_up")
        self.assertEqual(self.adapter.calls, [])

    def test_not_created_explicit_reconciliation_releases_slot_new_id_audits_retry(self):
        first = self.create(adapter=FakeAdapter(self.store, error=TimeoutError()))
        self.reconcile(first["follow_up_id"])
        second = self.create(policy={**POLICY, "daily_draft_cap": 1})
        self.assertEqual(second["status"], "draft_created")
        self.assertEqual(second["daily_count"], 1)
        self.assertNotEqual(first["follow_up_id"], second["follow_up_id"])
        self.assertEqual(len(self.store.load()[0]["outreach"]), 2)

    def test_manual_send_logging_is_private_idempotent_and_preserves_submission_time(self):
        result = self.create(channel="linkedin")
        kwargs = {"load_records": self.store.load, "persist_record": self.store.persist,
                  "occurred_at": "2026-10-20T10:05:00+08:00", "recorded_at": "2026-10-22T11:00:00+08:00",
                  "evidence": "private/manual-confirmation.txt"}
        for _ in range(2):
            logged = outreach.confirm_manual_send("app-a", result["follow_up_id"], **kwargs)
            self.assertEqual(logged["status"], "manual_send_recorded")
        record = self.store.load()[0]
        self.assertEqual(record["submitted_at"], SUBMITTED)
        self.assertEqual(len(record["outreach"][0]["manual_sends"]), 1)
        self.assertEqual(self.create()["reason"], "already_followed_up")
        with self.assertRaises(outreach.OutreachError):
            outreach.confirm_manual_send("app-a", result["follow_up_id"], **{**kwargs, "evidence": ""})
        self.assertEqual(self.adapter.calls, [])

    def test_confirmed_manual_send_on_uncertainty_cannot_be_released(self):
        result = self.create(adapter=FakeAdapter(self.store, error=TimeoutError()))
        outreach.confirm_manual_send("app-a", result["follow_up_id"], load_records=self.store.load,
                                     persist_record=self.store.persist, occurred_at=NOW, recorded_at=NOW,
                                     evidence="private/manual-confirmation.txt")
        with self.assertRaises(outreach.OutreachError):
            self.reconcile(result["follow_up_id"])
        self.assertEqual(self.create()["reason"], "draft_uncertain")
        self.assertEqual(self.reconcile(result["follow_up_id"], resolution="draft_created", adapter_id="observed-draft")["status"], "draft_created")

    def test_response_saved_only_in_generic_events_also_blocks(self):
        self.edit(lambda r: r["events"].append({"status": "interview", "at": NOW}))
        self.assertEqual(self.create()["reason"], "response_or_closed")

    def test_false_released_reservation_without_reconciliation_fails_closed(self):
        self.create()
        self.edit(lambda r: r["outreach"][0].update(status="not_created"))
        with self.assertRaises(outreach.OutreachError):
            self.create()

    def test_corrupt_history_and_naive_clock_fail_closed(self):
        with self.assertRaises(outreach.OutreachError):
            self.create(now="2026-10-20T10:00:00")
        self.edit(lambda r: r.update(outreach=[{"status": "draft_created"}]))
        with self.assertRaises(outreach.OutreachError):
            self.create()
        self.assertEqual(self.adapter.calls, [])

    def test_ast_adapter_surface_has_only_create_draft_no_network_imports(self):
        tree = ast.parse(Path(outreach.__file__).read_text())
        adapter_calls = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                         and node.func.value.id == "adapter"]
        self.assertEqual(adapter_calls, ["create_draft"])
        forbidden = {"send", "send_message", "reply", "messages", "invite", "send_invite"}
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr in forbidden for node in ast.walk(tree)))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertFalse(imports.intersection({"requests", "http", "smtplib", "subprocess", "socket"}))


if __name__ == "__main__":
    unittest.main()
