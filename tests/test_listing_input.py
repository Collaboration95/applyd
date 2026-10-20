"""Synthetic-only schema and stdin validation; no private workspace access."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from listing_input import ListingError, validate_listing

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/listing_input.py"


def legacy(**changes):
    return {"company": "Example Cedar Labs", "role": "Synthetic Engineer", "location": "Singapore",
            "platform": "linkedin", "platform_job_id": "900001",
            "listing_url": "https://www.linkedin.com/jobs/view/900001?trk=synthetic",
            "job_description": "  Complete synthetic text.\n\nSecond paragraph.\n", **changes}


def extracted(**changes):
    data = legacy()
    data["title"] = data.pop("role")
    data["source_url"] = data.pop("listing_url")
    return {**data, "source": "linkedin_job_page", "easy_apply": True, "apply_url": None,
            "canonical_url": "https://www.linkedin.com/jobs/view/900001",
            "destination_url": "https://www.linkedin.com/jobs/view/900001",
            "workplace_type": None, "employment_type": "Full-time", "posted_age": "2 days ago",
            "hiring_team": None, **changes}


class ListingInputTests(unittest.TestCase):
    def test_capture_cli_invalid_stdin_returns_two_without_partial_capture(self):
        workspace = SCRIPT.with_name("workspace.py")
        with tempfile.TemporaryDirectory() as directory:
            for data,field in [("{", "input"), (json.dumps({"company": "Example"}), "role")]:
                result = subprocess.run([sys.executable, str(workspace), "--workspace", directory, "capture", "--stdin"], input=data, text=True, capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn(field, result.stderr)
                self.assertFalse((Path(directory) / "applications/records").exists())

    def assert_field(self, data, field):
        before = copy.deepcopy(data)
        with self.assertRaises(ListingError) as caught:
            validate_listing(data)
        self.assertEqual(caught.exception.field, field)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn(field, str(caught.exception))
        self.assertEqual(data, before)

    def test_legacy_schema_remains_supported_without_mutating_description(self):
        data = legacy(applied_by=None, employer_requisition_id="SYNTHETIC-1", notes="Synthetic note")
        result = validate_listing(data)
        self.assertEqual(result, data)
        self.assertIsNot(result, data)
        self.assertEqual(result["job_description"], "  Complete synthetic text.\n\nSecond paragraph.\n")

    def test_extractor_aliases_and_optional_facts_survive_normalization(self):
        poster = {"name": "Example Hiring Person", "headline": None,
                  "profile_url": "https://www.linkedin.com/in/synthetic-person/"}
        data = extracted(hiring_team=poster)
        before = copy.deepcopy(data)
        result = validate_listing(data)
        self.assertEqual(data, before)
        self.assertEqual(result["role"], data["title"])
        self.assertEqual(result["listing_url"], data["canonical_url"])
        self.assertEqual(result["source_url"], data["source_url"])
        self.assertEqual(result["canonical_listing_url"], data["canonical_url"])
        self.assertIsNone(result["application_url"])
        self.assertEqual(result["hiring_team"], poster)
        self.assertIsNot(result["hiring_team"], poster)
        self.assertEqual(result["employment_type"], "Full-time")
        self.assertIsNone(result["workplace_type"])
        self.assertEqual(validate_listing(result), result)

    def test_external_destination_is_preserved_including_query(self):
        url = "https://careers.example.org/jobs/1?source=linkedin&campaign=synthetic"
        result = validate_listing(extracted(easy_apply=False, apply_url=url, destination_url=url))
        self.assertEqual(result["application_url"], url)
        self.assertEqual(result["destination_url"], url)

    def test_missing_or_invalid_required_fields(self):
        for field in ("company", "role", "location", "listing_url", "job_description"):
            for value in (None, "", "  ", [], 1, False):
                with self.subTest(field=field, value=value):
                    self.assert_field(legacy(**{field: value}), field)
        for data in ([], "text", None):
            self.assert_field(data, "input")

    def test_optional_fields_are_validated_before_returning_any_data(self):
        for field in ("platform", "platform_job_id", "employer_requisition_id", "source", "workplace_type",
                      "employment_type", "posted_age", "applied_by", "notes", "application_url"):
            with self.subTest(field=field):
                self.assert_field(legacy(**{field: {"invalid": "object"}}), field)
        for value in (None, "false", 1):
            self.assert_field(extracted(easy_apply=value), "easy_apply")
        self.assert_field(extracted(hiring_team=[]), "hiring_team")
        self.assert_field(extracted(hiring_team={"name": False}), "hiring_team.name")
        self.assert_field(extracted(hiring_team={"email": "not-allowed"}), "hiring_team.email")
        self.assert_field(extracted(hiring_team={"profile_url": "javascript:bad"}), "hiring_team.profile_url")

    def test_unsafe_or_malformed_urls_are_rejected(self):
        for value in ("/jobs/1", "javascript:bad", "file:///tmp/synthetic", "https://", "https://" + "user:pass@" + "example.org/",
                      "https://example.org/a\nb", "https://example.org:invalid/", "https://example.org:99999/",
                      "https://example.org\\evil"):
            with self.subTest(value=value):
                self.assert_field(legacy(listing_url=value), "listing_url")

    def test_conflicting_aliases_and_record_injection_are_rejected(self):
        for alias, target, value in (("title", "role", "Other role"), ("source_url", "listing_url", "https://example.org/jobs/1"),
                                      ("apply_url", "application_url", "https://example.org/jobs/1"),
                                      ("canonical_url", "canonical_listing_url", "https://example.org/jobs/1")):
            with self.subTest(alias=alias):
                self.assert_field(extracted(**{target: value}), target)
        for field in ("status", "captured_at", "job_description_path", "application_id", "submissions"):
            self.assert_field(extracted(**{field: "injected"}), field)

    def test_linkedin_identity_and_destination_consistency(self):
        for changes, field in (({"platform": "other"}, "platform"), ({"platform_job_id": "invalid"}, "platform_job_id"),
                               ({"canonical_url": "https://www.linkedin.com/jobs/view/900002"}, "canonical_listing_url"),
                               ({"source_url": "https://www.linkedin.com/jobs/view/900002"}, "listing_url"),
                               ({"source_url": "https://linkedin.com.example.org/jobs/view/900001"}, "listing_url"),
                               ({"destination_url": "https://example.org/jobs/1"}, "destination_url"),
                               ({"apply_url": "https://example.org/jobs/1"}, "application_url")):
            with self.subTest(changes=changes):
                self.assert_field(extracted(**changes), field)
        for missing, field in (("easy_apply", "easy_apply"), ("canonical_url", "canonical_listing_url"), ("destination_url", "destination_url")):
            data = extracted()
            del data[missing]
            self.assert_field(data, field)

    def test_search_page_identity_is_accepted_without_guessing_destination(self):
        data = extracted(source_url="https://www.linkedin.com/jobs/search/?currentJobId=900001&origin=SYNTHETIC",
                         easy_apply=False, destination_url=None)
        self.assertIsNone(validate_listing(data)["application_url"])
        result = validate_listing(extracted(source_url="https://www.linkedin.com/jobs/collections/recommended/"))
        self.assertEqual(result["platform_job_id"], "900001")
        self.assertEqual(result["listing_url"], "https://www.linkedin.com/jobs/view/900001")
        self.assertEqual(result["source_url"], "https://www.linkedin.com/jobs/collections/recommended/")
        self.assertEqual(validate_listing(result), result)

    def test_conflicting_or_duplicate_url_job_ids_are_rejected(self):
        for query in ("?currentJobId=900002", "?currentJobId=900001&currentJobId=900001", "?currentJobId=invalid", "?currentJobId="):
            self.assert_field(extracted(source_url="https://www.linkedin.com/jobs/view/900001" + query), "listing_url")

    def test_stdin_cli_validates_without_writing_files_and_returns_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            for payload, field in (("not JSON", "input"), ("[]", "input"),
                                   (json.dumps(extracted(hiring_team={"name": []})), "hiring_team.name")):
                result = subprocess.run([sys.executable, str(SCRIPT)], input=payload, text=True,
                                        capture_output=True, cwd=directory)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("ListingError", result.stderr)
                self.assertIn(field, result.stderr)
            result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(extracted()),
                                    text=True, capture_output=True, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), validate_listing(extracted()))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_normalized_payload_captures_canonical_identity_and_exact_optional_facts(self):
        import workspace
        source = "https://www.linkedin.com/jobs/collections/recommended/"
        data = extracted(source_url=source, hiring_team={"name": "Example Hiring Person", "headline": None, "profile_url": None})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
            result = workspace.capture(root, validate_listing(data), now)
            record = json.loads(Path(result["record"]).read_text())
            self.assertEqual(record["canonical_listing_url"], data["canonical_url"])
            self.assertEqual(record["source_url"], source)
            self.assertEqual(record["hiring_team"], data["hiring_team"])
            self.assertEqual((root / record["job_description_path"]).read_text(), data["job_description"])
            direct = extracted(source_url="https://www.linkedin.com/jobs/view/900001")
            duplicate = workspace.capture(root, validate_listing(direct), now)
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["application_id"], result["application_id"])


if __name__ == "__main__":
    unittest.main()
