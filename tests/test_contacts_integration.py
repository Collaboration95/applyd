"""Synthetic listing/contact/export integration; never open a live workspace."""
import copy
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from contacts import contacts_json
from export_dashboard import export_dashboard
from listing_input import ListingError, normalize_listing_contacts, validate_listing

NOW = datetime(2026, 10, 20, 12, 30, tzinfo=timezone(timedelta(hours=8)))
CARD = {"name": 'Synthetic 林, "Cedar"', "headline": "Example Hiring Lead",
        "profile_url": "https://www.linkedin.com/in/synthetic-cedar/"}


def listing(**changes):
    return {"company": "Synthetic Employer", "role": "Synthetic Engineer",
            "location": "Singapore", "platform": "linkedin", "platform_job_id": "900001",
            "source": "linkedin_job_page", "easy_apply": True,
            "listing_url": "https://www.linkedin.com/jobs/view/900001",
            "canonical_listing_url": "https://www.linkedin.com/jobs/view/900001",
            "destination_url": "https://www.linkedin.com/jobs/view/900001",
            "job_description": "Full synthetic description. Contact Cedar@example.org.",
            "hiring_team": copy.deepcopy(CARD), **changes}


class ContactIntegrationTests(unittest.TestCase):
    def test_card_gets_actual_capture_time_without_changing_extractor_shape(self):
        data = listing()
        before = copy.deepcopy(data)
        validated = validate_listing(data)
        self.assertNotIn("contacts", validated)
        captured = normalize_listing_contacts(validated, NOW)
        self.assertEqual(data, before)
        self.assertEqual(captured["hiring_team"], CARD)
        self.assertEqual(len(captured["contacts"]), 1)
        contact = captured["contacts"][0]
        self.assertEqual({key: contact[key] for key in CARD}, CARD)
        self.assertEqual(contact["source"], "linkedin_hiring_team")
        self.assertEqual(contact["captured_at"], NOW.isoformat())
        self.assertEqual(contact["relationship"], "hiring_team")
        self.assertIsNone(contact["email"])
        self.assertIs(contact["verified"], False)

    def test_missing_null_empty_and_partial_cards(self):
        for card in (None, {}, dict.fromkeys(CARD)):
            self.assertEqual(normalize_listing_contacts(validate_listing(listing(hiring_team=card)), NOW)["contacts"], [])
        data = listing()
        del data["hiring_team"]
        self.assertEqual(normalize_listing_contacts(validate_listing(data), NOW)["contacts"], [])
        partial = normalize_listing_contacts(validate_listing(listing(hiring_team={"headline": "Job poster"})), NOW)["contacts"][0]
        self.assertIsNone(partial["name"])
        self.assertEqual(partial["relationship"], "hiring_team")

    def test_optional_contacts_validate_without_reading_a_clock(self):
        for value in (None, []):
            self.assertEqual(validate_listing(listing(contacts=value))["contacts"], [])
        supplied = {"name": "Synthetic Person", "source": "jd_text"}
        data = listing(contacts=[supplied])
        before = copy.deepcopy(data)
        validated = validate_listing(data)
        self.assertEqual(data, before)
        self.assertIsNone(validated["contacts"][0]["captured_at"])
        self.assertEqual(validate_listing(validated), validated)
        persisted = normalize_listing_contacts(validated, NOW)
        self.assertEqual(persisted["contacts"][1]["captured_at"], NOW.isoformat())

    def test_sources_shape_metadata_and_emails_fail_before_persistence(self):
        base = {"name": "Synthetic Person", "source": "jd_text"}
        bad = [(value, "contacts") for value in ({}, False, "", 0)]
        bad += [([None], "contacts[0]")]
        bad += [([{**base, "source": value}], "contacts[0].source")
                for value in (None, "profile_visit", "enrichment", "linkedin_job_page", "other", [])]
        bad += [([{**base, key: value}], f"contacts[0].{key}") for key, value in (
            ("verified", True), ("extra", "guessed"), ("captured_at", "2026-10-20T12:30:00"),
            ("profile_url", "javascript:alert(1)"), ("contact_id", "forged"),
            ("email", "cedar@example.org"))]
        for contacts, field in bad:
            with self.subTest(contacts=contacts):
                data = listing(contacts=contacts)
                before = copy.deepcopy(data)
                with self.assertRaises(ListingError) as caught:
                    validate_listing(data)
                self.assertEqual(caught.exception.field, field)
                self.assertEqual(caught.exception.exit_code, 2)
                self.assertEqual(data, before)
                with self.assertRaises(ListingError):
                    normalize_listing_contacts(data, NOW)

    def test_exact_printed_jd_email_and_all_three_supported_sources(self):
        for source in ("linkedin_hiring_team", "jd_text", "confirmation_email"):
            captured = normalize_listing_contacts(validate_listing(listing(
                hiring_team=None, contacts=[{"name": "Synthetic Person", "source": source}]
            )), NOW)
            self.assertEqual(captured["contacts"][0]["source"], source)
        supplied = {"source": "jd_text", "email": "Cedar@example.org"}
        self.assertEqual(normalize_listing_contacts(validate_listing(listing(contacts=[supplied])), NOW)["contacts"][1]["email"], supplied["email"])
        with self.assertRaises(ListingError):
            validate_listing(listing(contacts=[{**supplied, "source": "confirmation_email"}]))

    def test_supplied_times_survive_and_card_duplicate_is_idempotent(self):
        initial = normalize_listing_contacts(validate_listing(listing()), NOW)
        retry = normalize_listing_contacts(validate_listing(initial), NOW)
        self.assertEqual(initial, retry)
        old = "2026-10-19T08:00:00+08:00"
        supplied = {"source": "jd_text", "name": "Another Synthetic Person", "captured_at": old}
        captured = normalize_listing_contacts(validate_listing(listing(contacts=[supplied])), NOW)
        self.assertEqual(captured["contacts"][1]["captured_at"], old)

    def test_invalid_source_rejected_by_stdin_cli_without_capture_files(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([
                sys.executable, "-B", str(ROOT / "scripts/workspace.py"),
                "--workspace", directory, "capture", "--stdin"],
                input=json.dumps(listing(contacts=[{"name": "Synthetic", "source": "profile_visit"}])),
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("contacts[0].source", result.stderr)
            for relative in ("applications/records", "applications/index", "applications/log.csv", "dashboard/data"):
                self.assertFalse((Path(directory) / relative).exists(), relative)

    def test_dashboard_snapshot_keeps_contacts_and_export_does_not_mutate_records(self):
        captured = normalize_listing_contacts(validate_listing(listing()), NOW)
        record = {**captured, "application_id": "synthetic-contact-app", "status": "discovered",
                  "captured_at": NOW.isoformat(), "status_updated_at": NOW.isoformat(),
                  "events": [], "submissions": []}
        before = copy.deepcopy(record)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_dashboard(root, snapshot=[record])
            payload = json.loads((root / "dashboard/data/applications.json").read_text())
            self.assertEqual(payload["applications"][0]["contacts"], record["contacts"])
            classic = (root / "dashboard/data.js").read_text()
            self.assertEqual(json.loads(classic.removeprefix("window.JOB_SEARCH_DATA = ").strip().removesuffix(";"))["applications"][0]["contacts"], record["contacts"])
            self.assertEqual(record, before)
            del record["contacts"]
            export_dashboard(root, snapshot=[record])
            self.assertEqual(json.loads((root / "dashboard/data/applications.json").read_text())["applications"][0]["contacts"], [])

    def test_csv_json_is_stable_unicode_and_roundtrips_punctuation(self):
        contacts = normalize_listing_contacts(validate_listing(listing()), NOW)["contacts"]
        contacts[0]["headline"] = 'Synthetic "title", with\na new line'
        text = contacts_json(contacts)
        self.assertEqual(contacts_json([dict(reversed(list(contacts[0].items())))]), text)
        self.assertIn("林", text)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=["contacts"])
        writer.writeheader()
        writer.writerow({"contacts": text})
        stream.seek(0)
        self.assertEqual(json.loads(next(csv.DictReader(stream))["contacts"]), contacts)
        self.assertEqual(contacts_json(None), "[]")
        self.assertEqual(contacts_json([]), "[]")


if __name__ == "__main__":
    unittest.main()
