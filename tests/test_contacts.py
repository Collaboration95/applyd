"""Synthetic, in-memory contact capture; no private files or live services."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from contacts import (ContactError, add_contact, from_hiring_team, remove_contact,
                      validate_contacts)


NOW = datetime(2026, 10, 20, 12, 30, tzinfo=timezone(timedelta(hours=8)))
LATER = NOW + timedelta(days=1)
PROFILE = "https://www.linkedin.com/in/synthetic-cedar-person/?trk=visible"


def card(**changes):
    return {"name": "Synthetic Cedar Person", "headline": "Example Hiring Lead",
            "profile_url": PROFILE, **changes}


def contact(**changes):
    return {**card(), "source": "linkedin_hiring_team", **changes}


class ContactTests(unittest.TestCase):
    def assert_invalid(self, contacts, field, **kwargs):
        before = deepcopy(contacts)
        with self.assertRaises(ContactError) as caught:
            validate_contacts(contacts, kwargs.pop("now", NOW), **kwargs)
        self.assertEqual(caught.exception.field, field)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn(field, str(caught.exception))
        self.assertEqual(contacts, before)

    def test_visible_card_preserves_exact_facts_and_adds_provenance(self):
        visible = card(name="  Synthetic Cedar Person  ")
        before = deepcopy(visible)
        contacts = from_hiring_team(visible, NOW)
        self.assertEqual(visible, before)
        self.assertEqual(len(contacts), 1)
        result = contacts[0]
        self.assertEqual(result["name"], visible["name"])
        self.assertEqual(result["headline"], visible["headline"])
        self.assertEqual(result["profile_url"], PROFILE)
        self.assertIsNone(result["email"])
        self.assertEqual(result["source"], "linkedin_hiring_team")
        self.assertEqual(result["captured_at"], NOW.isoformat())
        self.assertEqual(result["relationship"], "hiring_team")
        self.assertIs(result["verified"], False)
        self.assertRegex(result["contact_id"], r"^contact-[a-f0-9]{64}$")
        self.assertEqual(validate_contacts(contacts, None), contacts)

    def test_no_card_or_all_null_card_yields_no_contacts(self):
        for value in (None, {}, card(name=None, headline=None, profile_url=None)):
            with self.subTest(value=value):
                self.assertEqual(from_hiring_team(value, NOW), [])
        self.assertEqual(validate_contacts(None, NOW), [])
        self.assertEqual(validate_contacts([], NOW), [])

    def test_partial_card_does_not_infer_name_url_email_or_job_poster(self):
        result = from_hiring_team({"headline": "Job poster"}, NOW)[0]
        for field in ("name", "profile_url", "email"):
            self.assertIsNone(result[field])
        self.assertEqual(result["headline"], "Job poster")
        self.assertEqual(result["relationship"], "hiring_team")
        explicit = from_hiring_team(card(relationship="job_poster"), NOW)[0]
        self.assertEqual(explicit["relationship"], "job_poster")

    def test_card_cannot_inject_source_email_verification_or_other_metadata(self):
        for field, value in (("source", "profile_visit"), ("email", "cedar@example.org"),
                             ("verified", True), ("captured_at", NOW.isoformat()),
                             ("notes", "guessed")):
            with self.subTest(field=field), self.assertRaises(ContactError):
                from_hiring_team(card(**{field: value}), NOW)
        for value in ([], "Synthetic person", True):
            with self.subTest(value=value), self.assertRaises(ContactError):
                from_hiring_team(value, NOW)
        with self.assertRaises(ContactError):
            from_hiring_team({"relationship": "friend"}, NOW)

    def test_only_three_sources_are_allowed_and_missing_source_is_rejected(self):
        for source in ("linkedin_hiring_team", "jd_text", "confirmation_email"):
            self.assertEqual(validate_contacts([contact(source=source)], NOW)[0]["source"], source)
        for source in ("profile_visit", "linkedin_job_page", "enrichment", "", None, [], False):
            with self.subTest(source=source):
                self.assert_invalid([contact(source=source)], "contacts[0].source")
        missing = contact()
        del missing["source"]
        self.assert_invalid([missing], "contacts[0].source")

    def test_collection_objects_and_unknown_fields_are_strict(self):
        for value in ({}, "contact", True, ()):
            self.assert_invalid(value, "contacts")
        self.assert_invalid([None], "contacts[0]")
        self.assert_invalid([contact(company="Guessed employer")], "contacts[0].company")
        self.assert_invalid([{"source": "jd_text"}], "contacts[0]")
        for key in ("name", "headline", "profile_url", "email"):
            for value in ("", " \n", 1, False, [], {}):
                with self.subTest(key=key, value=value):
                    self.assert_invalid([contact(**{key: value})], f"contacts[0].{key}")

    def test_optional_facts_stay_null_and_relationship_defaults_to_unknown(self):
        result = validate_contacts([{"source": "jd_text", "name": "Synthetic Person"}], NOW)[0]
        self.assertEqual(result["relationship"], "unknown")
        for field in ("headline", "email", "profile_url"):
            self.assertIsNone(result[field])
        for relationship in ("job_poster", "hiring_team", "unknown"):
            self.assertEqual(validate_contacts([contact(relationship=relationship)], NOW)[0]["relationship"], relationship)
        for relationship in ("friend", "recruiter", "", None, True, []):
            self.assert_invalid([contact(relationship=relationship)], "contacts[0].relationship")

    def test_verification_cannot_be_set_or_coerced(self):
        for value in (True, "false", 0, 1, None, [], {}):
            self.assert_invalid([contact(verified=value)], "contacts[0].verified")
        self.assertIs(validate_contacts([contact(verified=False)], NOW)[0]["verified"], False)

    def test_offset_timestamp_is_required_and_supplied_now_is_the_only_fallback(self):
        for value in (NOW, NOW.isoformat(), "2026-10-20T04:30:00Z"):
            self.assertEqual(validate_contacts([contact()], value)[0]["captured_at"],
                             value.isoformat() if isinstance(value, datetime) else value)
        exact = "2026-10-19T09:00:00.120+05:30"
        self.assertEqual(validate_contacts([contact(captured_at=exact)], None)[0]["captured_at"], exact)
        self.assertEqual(validate_contacts([contact(captured_at=None)], NOW)[0]["captured_at"], NOW.isoformat())
        for value in (None, "2026-10-20", "2026-10-20T12:30:00", "yesterday", "",
                      "2026-02-30T00:00:00+08:00", "2026-10-20T00:00:00+00:99",
                      NOW.replace(tzinfo=None), False):
            with self.subTest(value=value):
                self.assert_invalid([contact()], "contacts[0].captured_at", now=value)
        for value in ("", "2026-10-20T12:30:00", False, NOW):
            self.assert_invalid([contact(captured_at=value)], "contacts[0].captured_at")

    def test_profile_url_is_preserved_but_unsafe_urls_are_rejected(self):
        for value in ("javascript:alert(1)", "/in/person", "file:///tmp/person", "https://",
                      "https://" + "user:pass@example.org/in/person", "https://example.org:99999/",
                      "https://example.org:bad/", "https://example.org/a\nb", "https://example.org\\bad"):
            with self.subTest(value=value):
                self.assert_invalid([contact(profile_url=value)], "contacts[0].profile_url")

    def test_printed_jd_email_is_exact_and_need_not_have_a_person_name(self):
        address = "Cedar.Hiring+jobs@example.org"
        data = [{"source": "jd_text", "email": address}]
        result = validate_contacts(data, NOW, jd_text=f"Send questions to <{address}>.\n")
        self.assertEqual(result[0]["email"], address)
        self.assertIsNone(result[0]["name"])
        self.assertIsNone(result[0]["profile_url"])
        self.assertIs(result[0]["verified"], False)

    def test_printed_confirmation_email_uses_confirmation_text(self):
        data = [contact(source="confirmation_email", email="cedar@example.org")]
        self.assertEqual(validate_contacts(data, NOW, email_text="Contact: cedar@example.org.")[0]["email"],
                         "cedar@example.org")
        self.assert_invalid(data, "contacts[0].email", jd_text="cedar@example.org")
        self.assert_invalid([contact(source="jd_text", email="cedar@example.org")],
                            "contacts[0].email", email_text="cedar@example.org")

    def test_hiring_team_email_still_requires_jd_or_confirmation_evidence(self):
        data = [contact(email="cedar@example.org")]
        self.assert_invalid(data, "contacts[0].email")
        for evidence in ({"jd_text": "cedar@example.org"}, {"email_text": "cedar@example.org"}):
            self.assertEqual(validate_contacts(data, NOW, **evidence)[0]["email"], "cedar@example.org")

    def test_guessed_obfuscated_case_changed_and_substring_emails_are_rejected(self):
        data = [contact(source="jd_text", email="cedar@example.org")]
        for text in (None, "Ask Synthetic Cedar Person", "cedar [at] example.org",
                     "Cedar@example.org", "xcedar@example.org", "cedar@example.org.evil.test",
                     "cedar@example.org-extra.test", "cedar@example.org@evil.org",
                     "cedar@example.org_foo", "prefix.cedar@example.org"):
            with self.subTest(text=text):
                self.assert_invalid(data, "contacts[0].email", jd_text=text)
        for value in ("cedar", "cedar@example", "cedar @example.org", "<cedar@example.org>"):
            self.assert_invalid([contact(email=value)], "contacts[0].email", jd_text=value)
        for field in ("jd_text", "email_text"):
            self.assert_invalid(data, field, **{field: []})

    def test_contact_id_is_deterministic_from_exact_visible_identity_and_source(self):
        first = validate_contacts([contact()], NOW)[0]
        retry = validate_contacts([dict(reversed(list(contact().items()))),
                                   contact(relationship="job_poster")], LATER)
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]["contact_id"], first["contact_id"])
        self.assertNotEqual(retry[0]["captured_at"], first["captured_at"])
        for changes in ({"source": "jd_text"}, {"name": "Other Synthetic Person"},
                        {"headline": "Other visible title"}, {"profile_url": None}):
            self.assertNotEqual(validate_contacts([contact(**changes)], NOW)[0]["contact_id"], first["contact_id"])
        self.assert_invalid([contact(contact_id="contact-forged")], "contacts[0].contact_id")

    def test_duplicate_contacts_keep_first_time_and_validate_every_entry(self):
        original = validate_contacts([contact()], NOW)[0]
        data = [original, contact(captured_at=LATER.isoformat())]
        self.assertEqual(validate_contacts(data, LATER), [original])
        self.assert_invalid([original, contact(source="profile_visit")], "contacts[1].source")

    def test_add_and_remove_are_deep_copies_preserving_submission_and_unrelated_fields(self):
        record = {"application_id": "synthetic-app", "status": "submitted",
                  "captured_at": "2026-10-01T09:00:00+08:00",
                  "submitted_at": "2026-10-02T10:00:00+08:00",
                  "submissions": [{"resume": {"sha256": "synthetic-hash"}}],
                  "outcome_events": [{"status": "acknowledged"}], "custom": {"flags": [1]}}
        before = deepcopy(record)
        added = add_contact(record, contact(), NOW)
        self.assertEqual(record, before)
        self.assertEqual({key: value for key, value in added.items() if key != "contacts"}, before)
        self.assertIsNot(added["submissions"], record["submissions"])
        retry = add_contact(added, contact(relationship="job_poster"), LATER)
        self.assertEqual(retry, added)
        self.assertIsNot(retry["contacts"][0], added["contacts"][0])
        removed = remove_contact(added, added["contacts"][0]["contact_id"])
        self.assertEqual(removed, {**before, "contacts": []})
        self.assertEqual(len(added["contacts"]), 1)
        removed["custom"]["flags"].append(2)
        self.assertEqual(record, before)
        self.assertEqual(added["custom"]["flags"], [1])

    def test_add_validates_incoming_email_without_revalidating_historical_contacts(self):
        record = add_contact({}, contact(source="jd_text", email="cedar@example.org"), NOW,
                             jd_text="cedar@example.org")
        result = add_contact(record, contact(name="Another Synthetic Person"), LATER)
        self.assertEqual(result["contacts"][0], record["contacts"][0])
        self.assertEqual(len(result["contacts"]), 2)
        before = deepcopy(record)
        with self.assertRaises(ContactError):
            add_contact(record, contact(verified=True), NOW)
        self.assertEqual(record, before)

    def test_remove_matches_only_requested_id_and_missing_id_is_idempotent(self):
        record = add_contact({}, contact(), NOW)
        record = add_contact(record, contact(source="jd_text"), NOW)
        first_id = record["contacts"][0]["contact_id"]
        result = remove_contact(record, first_id)
        self.assertEqual(result["contacts"], [record["contacts"][1]])
        self.assertEqual(remove_contact(result, first_id), result)
        self.assertEqual(remove_contact({}, first_id), {})
        for invalid in (None, [], "", " "):
            with self.subTest(invalid=invalid), self.assertRaises(ContactError):
                remove_contact(record, invalid)

    def test_record_mutators_reject_malformed_record_contacts(self):
        for record in (None, [], {"contacts": None}, {"contacts": {}}, {"contacts": [False]}):
            with self.subTest(record=record):
                with self.assertRaises(ContactError):
                    add_contact(record, contact(), NOW)
                with self.assertRaises(ContactError):
                    remove_contact(record, "contact-synthetic")


if __name__ == "__main__":
    unittest.main()
