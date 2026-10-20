"""Synthetic fixed-record coverage for weekly reports."""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reports
import metrics
from datetime import datetime


def application(app_id, *, submitted="2026-10-19T10:00:00+00:00", url=None,
                scope="backend", preset="software-backend", tags=None,
                channel="easy_apply", response=None, effort=None, blockers=None,
                company=None,
                stop_reason=None):
    record = {
        "application_id": app_id,
        "company": company or f"Synthetic Labs {app_id}",
        "role": "Backend Engineer",
        "location": "Singapore",
        "listing_url": url or f"https://jobs.example.test/{app_id}",
        "status": "submitted",
        "submissions": [{
            "status": "submitted", "submitted_at": submitted,
            "confirmation_path": "synthetic-confirmation.json",
            "scope": scope, "preset": preset,
            "role_tags": tags or ["backend"], "channel": channel,
            **({"effort_seconds": effort} if effort is not None else {}),
        }],
    }
    if response:
        record["outcome_events"] = [{"status": response[0], "occurred_at": response[1],
                                     "recorded_at": "2026-10-20T15:00:00+00:00"}]
    if blockers:
        record["blocked_on_answer_ids"] = blockers
    if stop_reason:
        record["stop_reason"] = stop_reason
        record["events"] = [{"at": submitted, "reason": stop_reason}]
    return record


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            application("synthetic-application-1", effort=120,
                        response=("interview", "2026-10-20T02:00:00+00:00"),
                        stop_reason="login_required"),
            # Same posting through a cross-post; it is one application, not two.
            application("synthetic-application-2", url="https://jobs.example.test/same",
                        submitted="2026-10-19T11:00:00+00:00", scope="role-specific",
                        preset="ai-agents", tags=["ai"], channel="external", effort=240,
                        company="Synthetic Crosspost"),
            application("synthetic-application-3", url="https://jobs.example.test/same",
                        submitted="2026-10-19T11:00:00+00:00", scope="role-specific",
                        preset="ai-agents", tags=["ai"], channel="external", effort=240,
                        company="Synthetic Crosspost"),
            application("synthetic-application-4", submitted="2026-10-21T01:00:00+00:00",
                        response=("withdrawn", "2026-10-21T02:00:00+00:00")),
            # This application's occurrence is outside the requested week.
            application("synthetic-application-5", submitted="2026-10-26T10:00:00+00:00",
                        response=("offer", "2026-10-26T11:00:00+00:00")),
            {"application_id": "synthetic-pending-1", "blocked_on_answer_ids": ["question-synthetic-1"]},
        ]

    def test_week_counts_unique_confirmations_and_occurring_responses(self):
        result = reports.weekly_report(self.records, "2026-W43", now="2026-10-22T12:00:00+00:00")
        self.assertEqual(result["totals"], {
            "applications": 3, "responses": 1, "response_rate": 0.3333,
            "median_effort_seconds": 180.0, "measured_effort_applications": 2,
            "provider_usage": None,
        })
        self.assertEqual(result["breakdowns"]["channel"], [
            {"group": "easy_apply", "applications": 2, "responses": 1, "response_rate": 0.5},
            {"group": "external", "applications": 1, "responses": 0, "response_rate": 0.0},
        ])
        self.assertEqual(result["stop_reasons"], [{"code": "login_required", "count": 1}])
        self.assertEqual(result["pending_questions"]["blocked"], [
            {"application_id": "synthetic-pending-1", "question_ids": ["question-synthetic-1"]},
        ])

    def test_same_input_is_stable_and_missing_effort_is_unavailable(self):
        records = [application("synthetic-no-usage", effort=None)]
        first = reports.weekly_report(records, "2026-10-19", now="2026-10-20T00:00:00Z")
        second = reports.weekly_report(records, "2026-10-19", now="2026-10-20T00:00:00Z")
        self.assertEqual(first, second)
        self.assertIsNone(first["totals"]["median_effort_seconds"])
        self.assertEqual(first["totals"]["measured_effort_applications"], 0)
        self.assertIsNone(first["totals"]["provider_usage"])
        self.assertIn("Median measured effort: Unavailable", reports.format_report(first))

    def test_recording_time_does_not_move_response_into_week(self):
        record = application("synthetic-delayed-response", submitted="2026-10-19T10:00:00Z",
                             response=("interview", "2026-10-18T23:59:00Z"))
        record["outcome_events"][0]["recorded_at"] = "2026-10-20T12:00:00Z"
        report = reports.weekly_report([record], "2026-W43")
        self.assertEqual(report["totals"]["responses"], 0)

    def test_shareable_report_uses_only_allowlisted_groups_and_codes(self):
        record = application("privateish-job-key", preset="secret-custom-preset",
                             scope="sensitive-resume-scope", tags=["Acme confidential role"],
                             stop_reason="customer-specific text")
        record["blocked_on_answer_ids"] = ["private-question-key"]
        result = reports.weekly_report([record], "2026-W43", shareable=True)
        rendered = reports.format_report(result)
        for private_marker in ("privateish-job-key", "secret-custom-preset",
                               "sensitive-resume-scope", "Acme confidential role",
                               "private-question-key", "customer-specific text"):
            self.assertNotIn(private_marker, rendered)
        self.assertEqual(result["stop_reasons"], [{"code": "other", "count": 1}])
        self.assertEqual(result["pending_questions"], {"applications": 1, "question_count": 1})
        self.assertEqual(result["breakdowns"]["preset"][0]["group"], "other")

    def test_invalid_usage_is_rejected_and_week_format_is_exact(self):
        for value in (float("inf"), float("nan"), -1, True):
            with self.assertRaises(ValueError):
                reports.weekly_report([application("synthetic-invalid", effort=value)], "2026-W43")
        with self.assertRaises(ValueError):
            reports.weekly_report([], "2026-X43")

    def test_requires_explicit_monday_and_valid_timezone(self):
        with self.assertRaises(ValueError):
            reports.weekly_report([], "2026-10-20")
        with self.assertRaises(ValueError):
            reports.weekly_report([], "2026-W43", timezone="not/a-zone")


class MeasuredWeeklyReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def action(self, sequence, stage, app='synthetic-measured', *, duration=2,
               at='2026-10-19T10:00:00+00:00', usage=None, complete=True):
        metrics.reserve(self.root, 'synthetic-run', sequence, stage, app, datetime.fromisoformat(at))
        if complete:
            metrics.observe(self.root, 'synthetic-run', sequence, duration, usage=usage)
            metrics.reconcile(self.root, 'synthetic-run', sequence, 'verified')

    def test_measured_weekly_snapshot_and_legacy_unknown_coverage(self):
        for i, stage in enumerate(('prepare', 'review', 'submit'), 1):
            self.action(i, stage, duration=i, usage={'tokens': i, 'cost': 0})
        records = [application('synthetic-measured', response=('interview', '2026-10-20T10:00:00Z')),
                   application('synthetic-legacy')]
        events = metrics.load_events(self.root)
        report = reports.weekly_report(records, '2026-W43', metrics=events)
        self.assertEqual(report['totals'], {'applications': 2, 'responses': 1, 'response_rate': .5,
            'median_effort_seconds': 6, 'measured_effort_applications': 1,
            'provider_usage': {'tokens': 6, 'cost': 0}})
        self.assertEqual(report['metrics']['confirmed_without_effort'], 1)
        self.assertEqual(report, reports.weekly_report(reversed(records), '2026-W43', metrics=reversed(events)))
        self.assertIn('1/2 confirmed applications with complete stage coverage', reports.format_report(report))
        self.assertIn('tokens: 6; cost: 0', reports.format_report(report))

    def test_partial_interrupted_telemetry_does_not_become_full_effort_or_cost(self):
        self.action(1, 'prepare', duration=12, usage={'tokens': 100})
        self.action(2, 'review', duration=3, usage={'tokens': 20, 'cost': .01})
        self.action(3, 'submit', complete=False)
        report = reports.weekly_report([application('synthetic-measured')], '2026-W43',
                                       metrics=metrics.load_events(self.root))
        self.assertIsNone(report['totals']['median_effort_seconds'])
        self.assertEqual(report['totals']['measured_effort_applications'], 0)
        self.assertEqual(report['totals']['provider_usage'], {'tokens': None, 'cost': None})
        self.assertEqual(report['metrics']['confirmed_with_partial_effort'], 1)
        self.assertEqual(report['metrics']['provider_coverage']['measured_tokens'], 120)
        self.assertIn('total unavailable; measured subtotal 120 (2/3 actions)', reports.format_report(report))

    def test_cohort_effort_includes_previous_week_measurements_but_week_usage_does_not(self):
        self.action(1, 'prepare', at='2026-10-18T10:00:00+00:00', duration=5, usage={'tokens': 90})
        self.action(2, 'review', duration=3, usage={'tokens': 7})
        self.action(3, 'submit', duration=2, usage={'tokens': 8})
        # Late logging/other work after first confirmation cannot raise its effort.
        self.action(4, 'prepare', at='2026-10-21T10:00:00+00:00', duration=999, usage={'tokens': 1})
        self.action(5, 'prepare', app='synthetic-next-week', at='2026-10-26T00:00:00+00:00', usage={'tokens': 500})
        report = reports.weekly_report([application('synthetic-measured')], '2026-W43', metrics=metrics.load_events(self.root))
        self.assertEqual(report['totals']['median_effort_seconds'], 10)
        self.assertEqual(report['totals']['provider_usage'], {'tokens': 16, 'cost': None})
        self.assertEqual(report['metrics']['reserved_actions'], 3)
        self.assertEqual(report['metrics']['unique_confirmed_applications'], 1)

    def test_shareable_json_removes_every_source_identifier_and_content(self):
        marker = 'CANARY-PRIVATE-source-company-question-resume'
        record = application(marker, scope=marker, preset=marker, tags=[marker], company=marker,
            url='https://jobs.example.test/' + marker, blockers=[marker], stop_reason=marker)
        record['submissions'][0]['confirmation_path'] = marker
        for i, stage in enumerate(('prepare', 'review', 'submit'), 1):
            self.action(i, stage, app=marker, usage={'tokens': i})
        events = metrics.load_events(self.root)
        private = reports.weekly_report([record], '2026-W43', metrics=events)
        self.assertIn(marker, json.dumps(private))
        shareable = reports.weekly_report([record], '2026-W43', metrics=events, shareable=True)
        encoded = json.dumps(shareable, allow_nan=False)
        for canary in (marker, 'synthetic-run', 'example.test'):
            self.assertNotIn(canary, encoded)
            self.assertNotIn(canary, reports.format_report(shareable))
        self.assertEqual(shareable['metrics']['unique_confirmed_applications'], 1)
        self.assertEqual(shareable['pending_questions'], {'applications': 1, 'question_count': 1})

    def test_missing_telemetry_never_uses_recording_timestamp_as_effort(self):
        record = application('synthetic-old')
        record.update(captured_at='2026-10-19T01:00:00Z', status_updated_at='2026-10-20T01:00:00Z')
        report = reports.weekly_report([record], '2026-W43', metrics=[])
        self.assertIsNone(report['totals']['median_effort_seconds'])
        self.assertEqual(report['metrics']['confirmed_without_effort'], 1)
        self.assertEqual(report['metrics']['reserved_applications'], 0)


if __name__ == "__main__":
    unittest.main()
