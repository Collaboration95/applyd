import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gmail_sync
from gmail_sync import classify_message, plan_sync, sync_with_adapter


def message(message_id, subject, body="", **extra):
    return {
        "message_id": message_id,
        "thread_id": "thread-" + message_id,
        "received_at": "2026-10-20T09:30:00+08:00",
        "subject": subject,
        "body": body,
        **extra,
    }


def application(app_id="app-1", **extra):
    return {
        "application_id": app_id,
        "company": "Example Labs",
        "role": "Backend Engineer",
        "platform_job_id": "listing-7",
        "status": "submitted",
        "submitted_at": "2026-10-18T10:00:00+08:00",
        "confirmation_path": "applications/records/" + app_id + "/confirmation.txt",
        "confirmation_sha256": "a" * 64,
        **extra,
    }


class GmailSyncTests(unittest.TestCase):
    def test_conservative_cue_table_covers_all_outcomes(self):
        cases = (
            ("Thank you for applying", "", "acknowledged"),
            ("Online assessment", "Please complete the test", "assessment"),
            ("Interview invitation", "We would like to meet", "interview"),
            ("Application update", "We will not be moving forward with your application", "rejected"),
            ("Recruiter note", "I am a recruiter and reaching out about an opportunity", "recruiter_message"),
            ("Company newsletter", "The office is moving next month", "unrelated"),
        )
        for subject, body, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_message(message("m", subject, body))["category"], expected)

    def test_negation_and_conflicting_cues_need_review(self):
        negated = classify_message(message("m1", "Application update", "We did not receive your application"))
        conflict = classify_message(message("m2", "Interview invitation", "Your application was unsuccessful"))
        self.assertTrue(negated["needs_review"])
        self.assertEqual(negated["reason"], "negated_outcome_cue")
        self.assertTrue(conflict["needs_review"])
        self.assertEqual(conflict["reason"], "conflicting_outcome_cues")

    def test_proposal_cites_message_id_and_offset_occurrence_time(self):
        result = plan_sync(
            [application()],
            [message("mail-1", "Interview invitation", company="Example Labs", role="Backend Engineer")],
        )
        self.assertEqual(result["proposals"], [{
            "application_id": "app-1",
            "status": "interview",
            "message_id": "mail-1",
            "occurred_at": "2026-10-20T09:30:00+08:00",
            "matched_by": "company_role",
            "matched_cue": ["interview invitation"],
        }])
        self.assertEqual(result["processed_message_ledger"], ["mail-1"])

    def test_listing_id_match_requires_unique_confirmed_submission(self):
        by_id = plan_sync(
            [application()],
            [message("mail-id", "Coding test", platform_job_id="listing-7")],
        )
        self.assertEqual(by_id["proposals"][0]["matched_by"], "platform_job_id")

        ambiguous = plan_sync(
            [application(), application("app-2")],
            [message("mail-ambiguous", "Interview invitation", company="Example Labs", role="Backend Engineer")],
        )
        self.assertEqual(ambiguous["proposals"], [])
        self.assertEqual(ambiguous["attention_queue"][0]["reason"], "ambiguous_application_match")

        unsubmitted = plan_sync(
            [application(status="queued", submitted_at="")],
            [message("mail-queued", "Thank you for applying", platform_job_id="listing-7")],
        )
        self.assertEqual(unsubmitted["proposals"], [])
        self.assertEqual(unsubmitted["attention_queue"][0]["reason"], "application_not_confirmed")

    def test_recommendation_or_alert_does_not_create_application_match(self):
        result = plan_sync([], [message("alert", "Interview invitation", company="Example Labs", role="Backend Engineer")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["attention_queue"][0]["reason"], "no_application_match")

    def test_status_or_timestamp_without_confirmation_evidence_is_not_confirmed(self):
        for record in (application(confirmation_path="", confirmation_sha256=""),
                       application(submitted_at=""),
                       application(confirmation_sha256="not-a-hash")):
            result = plan_sync([record], [message("unconfirmed", "Interview invitation", platform_job_id="listing-7")])
            self.assertEqual(result["proposals"], [])
            self.assertEqual(result["attention_queue"][0]["reason"], "application_not_confirmed")

    def test_job_id_does_not_override_contradictory_company_or_role(self):
        for identity in ({"company": "Other Labs"}, {"role": "Sales Engineer"}):
            result = plan_sync([application()], [message("conflict", "Coding test", platform_job_id="listing-7", **identity)])
            self.assertEqual(result["proposals"], [])
            self.assertEqual(result["attention_queue"][0]["reason"], "contradictory_application_identity")

    def test_received_time_must_not_predate_confirmation(self):
        result = plan_sync([application()], [message("old", "Interview invitation", platform_job_id="listing-7",
                                                       received_at="2026-10-18T01:59:59Z")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["attention_queue"][0]["reason"], "message_predates_confirmation")

    def test_submission_entry_with_evidence_is_accepted_after_outcome_status_change(self):
        record = application(status="assessment", submitted_at="", confirmation_path="", submissions=[{
            "submitted_at": "2026-10-18T10:00:00+08:00", "confirmation_path": "confirmation.txt",
            "confirmation_sha256": "a" * 64}])
        result = plan_sync([record], [message("reply", "Interview invitation", platform_job_id="listing-7")])
        self.assertEqual(result["proposals"][0]["status"], "interview")

    def test_replay_is_deduplicated_and_pending_message_can_be_retried(self):
        msg = message("mail-2", "Application received", company="Example Labs", role="Backend Engineer")
        first = plan_sync([application()], [msg])
        second = plan_sync([application()], [msg], first)
        self.assertEqual(second["proposals"], [])
        self.assertEqual(second["processed_message_ledger"], ["mail-2"])

        pending_msg = message("mail-pending", "Interview invitation", company="Example Labs", role="Backend Engineer")
        pending = plan_sync([], [pending_msg])
        retried = plan_sync([application()], [pending_msg], pending)
        self.assertEqual(len(retried["proposals"]), 1)
        self.assertEqual(retried["attention_queue"], [])

    def test_queued_full_message_replays_without_resupply_after_cursor_advance(self):
        pending = message("old-pending", "Interview invitation", platform_job_id="listing-7")
        state = plan_sync([], [pending, message("latest", "Newsletter", received_at="2026-10-21T09:00:00+08:00")])
        self.assertEqual(state["attention_queue"][0]["message"], pending)
        result = plan_sync([application()], [], state)
        self.assertEqual(result["proposals"][0]["message_id"], "old-pending")
        self.assertEqual(result["attention_queue"], [])

    def test_processed_id_clears_stale_attention_after_recovery(self):
        state = plan_sync([], [message("pending", "Coding test", platform_job_id="listing-7")])
        state["processed_message_ledger"].append("pending")
        result = plan_sync([application()], [], state)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["attention_queue"], [])

    def test_cursor_uses_latest_offset_aware_message_time(self):
        result = plan_sync([], [
            message("later", "Newsletter", received_at="2026-10-20T02:00:00Z"),
            message("earlier", "Newsletter", received_at="2026-10-20T09:00:00+08:00"),
        ])
        self.assertEqual(result["cursor"], "2026-10-20T02:00:00Z")

    def test_missing_adapter_returns_typed_credential_blocker(self):
        self.assertEqual(sync_with_adapter(None, [], {}), {
            "status": "blocked",
            "blocker": {
                "type": "credential_required",
                "reason": "live_verification_requires_credentials",
            },
        })

    def test_adapter_path_uses_search_list_get_only(self):
        class SyntheticReadOnlyAdapter:
            def __init__(self):
                self.calls = []

            def search(self, *, since):
                self.calls.append(("search", since))
                return ["mail-adapter"]

            def list(self, message_ids):
                self.calls.append(("list", tuple(message_ids)))
                return [{"message_id": "mail-adapter"}]

            def get(self, message_id):
                self.calls.append(("get", message_id))
                return message(message_id, "Interview invitation", platform_job_id="listing-7")

        adapter = SyntheticReadOnlyAdapter()
        result = sync_with_adapter(adapter, [application()], {"cursor": "2026-10-19T00:00:00Z"})
        self.assertEqual([call[0] for call in adapter.calls], ["search", "list", "get"])
        self.assertEqual(result["proposals"][0]["message_id"], "mail-adapter")

    def test_adapter_contract_and_implementation_have_only_read_operations(self):
        tree = ast.parse(Path(gmail_sync.__file__).read_text())
        forbidden = {"send", "reply", "delete", "modify", "label", "create_event", "insert_event"}
        methods = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue({"search", "list", "get"}.issubset(methods))
        calls = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(forbidden & (methods | calls))

    def test_adapter_refetches_pending_ids_even_when_search_and_list_omit_them(self):
        class Adapter:
            def search(self, *, since):
                self.since = since
                return []

            def list(self, message_ids):
                self.ids = message_ids
                return []

            def get(self, message_id):
                return message(message_id, "Interview invitation", platform_job_id="listing-7")

        adapter = Adapter()
        state = {"cursor": "2026-10-21T00:00:00Z", "attention_queue": [{"message_id": "old-pending"}]}
        result = sync_with_adapter(adapter, [application()], state)
        self.assertEqual(adapter.ids, ["old-pending"])
        self.assertEqual(result["proposals"][0]["message_id"], "old-pending")

    def test_adapter_message_identity_mismatch_is_refused(self):
        class Adapter:
            def search(self, *, since):
                return ["requested"]

            def list(self, message_ids):
                return []

            def get(self, message_id):
                return message("different", "Interview invitation")

        with self.assertRaisesRegex(ValueError, "adapter message_id mismatch"):
            sync_with_adapter(Adapter(), [], {})


if __name__ == "__main__":
    unittest.main()
