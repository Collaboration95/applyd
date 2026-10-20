"""Synthetic boundary fixtures only; no private workspace or live actions."""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eligibility as e
import settings


def listing(description="Build and maintain software.", **fields):
    return {"company": "Synthetic Labs", "title": "Engineer", "location": "Example City",
            "employment_type": "Full-time", "job_description": description,
            "listing_url": "https://www.linkedin.com/jobs/view/900000", **fields}


def confirmed(value, **fields):
    return {"value": value, "current": True, "confirmed": True, **fields}


class EligibilityTests(unittest.TestCase):
    def check(self, description, decision, code=None, policy=None, facts=None, **fields):
        result = e.evaluate(listing(description, **fields), policy or {"max_required_years": 3}, facts)
        self.assertEqual(result["decision"], decision, description)
        if code:
            self.assertIn(code, result["reason_codes"], description)
        return result

    def test_explicit_experience_boundary_table(self):
        cases = [
            ("3+ years of experience.", "eligible"),
            ("Minimum 3 years of experience required.", "eligible"),
            ("At least 4 years of experience required.", "excluded"),
            ("Minimum 4 years of professional experience.", "excluded"),
            ("Must have 4 years of relevant software engineering experience.", "excluded"),
            ("4 years of experience is mandatory.", "excluded"),
            ("4+ yrs experience.", "excluded"),
            ("3-5 years of experience.", "eligible"),
            ("3–5 years of experience required.", "eligible"),
            ("4—6 years of experience.", "excluded"),
            ("4 to 6 years of experience.", "excluded"),
            ("Between 3 and 5 years of experience required.", "eligible"),
            ("3 years to 5 years of experience required.", "eligible"),
            ("Between 4 and 6 years of experience required.", "excluded"),
            ("3.5 years of experience required.", "excluded"),
            ("Not less than 4 years of experience.", "excluded"),
            ("No less than 4 years of experience required.", "excluded"),
            ("Min. 4 years of experience.", "excluded"),
            ("4 years of experience is required, not preferred.", "excluded"),
            ("Requirements:\n- 4 years of experience", "excluded"),
            ("Experience required: 4 years", "excluded"),
            ("4 years in software development required.", "excluded"),
            ("4 yrs exp required.", "excluded"),
        ]
        for text, decision in cases:
            with self.subTest(text=text):
                self.check(text, decision, "skipped:required_years" if decision == "excluded" else None)

    def test_preferences_and_negations_never_become_hard_exclusions(self):
        for text in [
            "4 years of experience preferred.",
            "4+ years of experience preferred.",
            "4–6 years of experience desirable.",
            "Ideally 4 years of experience.",
            "4 years of experience is a bonus.",
            "4+ years of experience is optional.",
            "4+ years of experience is nice-to-have.",
            "4+ years of experience would be beneficial.",
            "Preferred qualifications:\n- 4 years of experience",
            "4 years of experience is not required.",
            "4 years of experience preferred, not required.",
            "We do not require 4 years of experience.",
            "We don't require 4 years of experience.",
            "No prior experience required.",
            "Requirements:\n4 years of experience is not mandatory.",
            "A 4-year degree is required.",
            "Our company was founded 4 years ago.",
        ]:
            with self.subTest(text=text):
                self.check(text, "eligible")

    def test_ambiguous_experience_never_guessed(self):
        for text in [
            "4 years of experience.",
            "Minimum 4 years required.",
            "4+ years.",
            "About 4 years of experience required.",
            "Up to 4 years of experience required.",
            "At most 4 years of experience required.",
            "A maximum of 4 years of experience required.",
            "More than 3 years of experience required.",
            "Less than 4 years of experience required.",
            "4 years of experience or an equivalent qualification.",
            "At least 4 years of experience unless otherwise qualified.",
            "Four years of experience required.",
            "4 years of experience may be required.",
            "4 years of experience is not preferred.",
            "Must have 4 years of experience preferred.",
            "4 years of experience not required, but 5 years required.",
            "4 years of experience preferred but 2 years required.",
            "5-3 years of experience required.",
            "3/4 years of experience required.",
            "1,000 years of experience required.",
            "4 years of experience required if no degree.",
            "Our team has 40+ years of experience.",
        ]:
            with self.subTest(text=text):
                self.check(text, "needs_review", "experience_ambiguous")

    def test_experience_scope_does_not_bleed_across_sentences_or_headings(self):
        self.check("4 years of experience preferred. At least 5 years of experience required.", "excluded")
        self.check("Preferred qualifications:\n4 years of experience\nRequirements:\n4 years of experience", "excluded")
        self.check("Preferred qualifications:\n4 years of experience\nResponsibilities:\n4 years of experience", "needs_review")
        self.check("Preferred qualifications:\n4 years of experience\nOther skills:\n4 years of experience", "needs_review")

    def test_cap_is_configured_and_never_inferred_from_candidate(self):
        text = "Minimum 4 years of experience required."
        self.assertEqual(e.evaluate(listing(text), {})["decision"], "eligible")
        self.assertEqual(e.evaluate(listing(text), {"max_required_years": None})["decision"], "eligible")
        self.check(text, "eligible", policy={"max_required_years": 4})
        self.check("1 year of experience required.", "excluded", policy={"max_required_years": 0})
        result = self.check(text, "excluded", facts={"professional_years": confirmed(20),
                                                    "technology": "Python", "degree": "Example degree"})
        self.assertEqual(result["used_facts"], {})

    def test_explicit_structured_minimum_reuses_settings(self):
        self.check("Build software.", "excluded", "skipped:required_years", required_years=4)
        self.check("Build software.", "eligible", required_years=3)
        for value in [True, "3+", -1, float("nan"), float("inf")]:
            with self.subTest(value=str(value)), self.assertRaises(settings.SettingsError):
                e.evaluate(listing(required_years=value), {})

    def test_settings_header_checks_are_reused_without_policy_drift(self):
        cases = [
            ({"easy_apply_only": True}, {"easy_apply": False}, "skipped:external_apply"),
            ({"company_blocklist": ["synthetic"]}, {}, "skipped:blocklist"),
            ({"title_blocklist": ["^Engineer$"]}, {}, "skipped:blocklist"),
            ({}, {"application_url": "javascript:alert(1)"}, "skipped:invalid_application_url"),
            ({"external_hosts": {"deny": ["example.invalid"]}},
             {"application_url": "https://jobs.example.invalid/900"}, "skipped:external_host"),
            ({"external_hosts": {"allow": ["example.invalid"]}},
             {"application_url": "https://example.invalid.evil.invalid/900"}, "skipped:external_host"),
        ]
        for policy, fields, reason in cases:
            with self.subTest(reason=reason):
                data = listing(**fields)
                with patch.object(e.settings, "eligibility", wraps=settings.eligibility) as gate:
                    result = e.evaluate(data, policy)
                    gate.assert_called_once_with(data, policy)
                self.assertEqual(result["decision"], "excluded")
                self.assertIn(reason, result["reason_codes"])

    def test_location_and_employment_policy_only_when_explicit(self):
        data = listing(location="Other City", employment_type="Contract")
        self.assertEqual(e.evaluate(data, {})["decision"], "eligible")
        policy = {"role_rules": {"allowed_locations": ["Example City"],
                                  "allowed_employment_types": ["Full-time", "Internship"]}}
        result = e.evaluate(data, policy)
        self.assertEqual(result["decision"], "excluded")
        self.assertEqual(result["reason_codes"], ["employment_type_not_allowed", "location_not_allowed"])
        data.update(location="  EXAMPLE city  ", employment_type="INTERNSHIP")
        self.assertEqual(e.evaluate(data, policy)["decision"], "eligible")
        self.assertEqual(e.evaluate(listing(location="Not Example City"), policy)["decision"], "excluded")

    def test_location_unknown_alternatives_and_explicit_denials(self):
        policy = {"role_rules": {"allowed_locations": ["Example City"]}}
        for location, reason in [(None, "location_unknown"), ("", "location_unknown"),
                                 ("Example City or Other City", "location_ambiguous"),
                                 ("Example City / Other City", "location_ambiguous")]:
            with self.subTest(location=location):
                self.check("Build software.", "needs_review", reason, policy=policy, location=location)
        policy["role_rules"]["excluded_locations"] = ["Example City"]
        self.check("Build software.", "excluded", "location_excluded", policy=policy)
        self.check("Build software.", "needs_review", "employment_type_unknown",
                   policy={"role_rules": {"allowed_employment_types": ["Full-time"]}}, employment_type=None)
        self.check("Build software.", "excluded", "employment_type_excluded",
                   policy={"role_rules": {"excluded_employment_types": ["Full-time"]}})
        self.check("Build software.", "eligible", policy={"role_rules": {"allowed_locations": None,
                                                                         "allowed_employment_types": []}})

    def test_sponsorship_requires_current_confirmed_fact(self):
        texts = ["We cannot sponsor visas.", "We do not provide visa sponsorship.",
                 "Visa sponsorship is not available.", "No visa sponsorship available.",
                 "We are unable to provide sponsorship.", "We will not sponsor work permits.",
                 "We are not able to sponsor visas.", "We can not sponsor visas.",
                 "Visa sponsorship cannot be provided.", "Sponsorship will not be offered.",
                 "No visa sponsorship is available.", "Visa sponsorship is not possible.",
                 "Applicants who require visa sponsorship are not eligible."]
        for text in texts:
            with self.subTest(text=text):
                self.check(text, "excluded", "sponsorship_unavailable",
                           facts={"sponsorship_required": confirmed(True)})
                self.check(text, "eligible", facts={"sponsorship_required": confirmed(False)})
                self.check(text, "needs_review", "sponsorship_required_unknown")
        for record in [True, False, None, {"value": True}, confirmed(True, current=False),
                       confirmed(True, confirmed=False), confirmed("true"), confirmed(True, current=1)]:
            with self.subTest(record=record):
                self.check(texts[0], "needs_review", "sponsorship_required_unknown",
                           facts={"sponsorship_required": record})

    def test_sponsorship_negation_ambiguity_and_unrelated_uses(self):
        for text in ["We can sponsor visas.", "Visa sponsorship is available.",
                     "We cannot sponsor the conference.", "No event sponsorship is available."]:
            with self.subTest(text=text):
                self.check(text, "eligible", facts={"sponsorship_required": confirmed(True)})
        for text in ["Visa sponsorship may not be available.", "We cannot guarantee visa sponsorship.",
                     "We are not unable to sponsor visas.", "Visa sponsorship is not required.",
                     "Visa sponsorship is not unavailable.", "No visa sponsorship is required.",
                     "No sponsorship needed.", "Sponsorship is available for other roles only.",
                     "We can sponsor visas but not for every position.",
                     "We cannot sponsor visas unless conditions change.",
                     "We cannot sponsor visas. Visa sponsorship is available.",
                     "Sponsorship will be discussed."]:
            with self.subTest(text=text):
                result = self.check(text, "needs_review", "sponsorship_ambiguous",
                                    facts={"sponsorship_required": confirmed(True)})
                self.assertEqual(result["used_facts"], {})

    def test_no_profile_inference_or_unrelated_fact_disclosure(self):
        facts = {"address": "Example City", "pass": "Student Pass", "degree": "Example degree",
                 "technology": "Python", "legally_authorized_to_work": confirmed(True),
                 "secret_unused_field": "synthetic canary"}
        result = self.check("We cannot sponsor visas.", "needs_review", "sponsorship_required_unknown", facts=facts)
        self.assertEqual(result["used_facts"], {})
        facts["sponsorship_required"] = confirmed(True, source="synthetic source")
        result = self.check("We cannot sponsor visas.", "excluded", facts=facts)
        self.assertEqual(result["used_facts"], {"sponsorship_required": confirmed(True)})
        self.assertNotIn("canary", json.dumps(result))

    def test_credit_and_work_authorization_are_distinct_explicit_facts(self):
        for text, key, reason in [
            ("Credit-bearing internship required.", "credit_bearing", "credit_bearing_requirement"),
            ("You must have the legal right to work.", "legally_authorized_to_work", "work_authorization_requirement"),
        ]:
            with self.subTest(text=text):
                self.check(text, "needs_review", reason + "_unknown")
                self.check(text, "eligible", facts={key: confirmed(True)})
                self.check(text, "excluded", reason + "_unmet", facts={key: confirmed(False)})
                self.check(text, "needs_review", reason + "_unknown",
                           facts={"sponsorship_required": confirmed(False), "pass": "Student Pass"})
        self.check("Credit-bearing internship preferred.", "eligible")
        self.check("Academic credit is not required.", "eligible")
        self.check("Credit-bearing or other endorsed internship required.", "needs_review")
        self.check("University endorsement required.", "needs_review", "credit_requirement_ambiguous")
        self.check("Credit-bearing preferred, but legal right to work required.", "needs_review")
        self.check("Academic credit not required, but credit-bearing placement required.", "needs_review")
        self.check("Preferred qualifications:\nCredit-bearing placement required.", "needs_review")

    def test_unknown_language_and_legal_categories_are_not_eligible(self):
        for text in ["Citizens or permanent residents only.", "A valid work permit is required.",
                     "Security clearance required.", "Must be fluent in Exampleish.",
                     "Exampleish language proficiency required.", "Exampleish speaking required.",
                     "Mandarin required.", "English preferred but French required.",
                     "English preferred, French required.", "English not required, French required.",
                     "Must be eligible to work locally.", "Work authorization required.",
                     "Local candidates only.", "Nationals only.", "PRs only."]:
            with self.subTest(text=text):
                self.check(text, "needs_review", facts={"legally_authorized_to_work": confirmed(True)})
        self.check("Exampleish proficiency preferred.", "eligible")
        self.check("Exampleish fluency is not required.", "eligible")
        self.check("English preferred, not required.", "eligible")
        self.check("Knowledge of a programming language required.", "eligible")

    def test_stable_json_nonmutation_and_exclusion_precedence(self):
        data = listing("We cannot sponsor visas. Four years of experience required.")
        policy = {"max_required_years": 3, "role_rules": {"excluded_locations": ["Example City"]}}
        facts = {"sponsorship_required": confirmed(True)}
        before = copy.deepcopy((data, policy, facts))
        result = e.evaluate(data, policy, facts)
        self.assertEqual(result["decision"], "excluded")
        self.assertEqual(result["reason_codes"], ["experience_ambiguous", "location_excluded", "sponsorship_unavailable"])
        self.assertEqual(e.evaluate(data, policy, facts), result)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual((data, policy, facts), before)
        result["used_facts"]["sponsorship_required"]["value"] = False
        self.assertEqual((data, policy, facts), before)

    def test_partial_listings_and_bad_inputs_fail_closed(self):
        self.assertEqual(e.evaluate({}, {})["decision"], "needs_review")
        self.assertEqual(e.evaluate({}, {})["reason_codes"], ["description_missing"])
        for data, policy, facts in [([], {}, None), ({}, [], None), ({}, {}, []),
                                     ({"job_description": 4}, {}, None),
                                     ({}, {"role_rules": {"country": "Example"}}, None),
                                     ({}, {"role_rules": {"allowed_locations": "Example"}}, None),
                                     ({}, {"role_rules": {"allowed_locations": [None]}}, None)]:
            with self.subTest(data=data, policy=policy), self.assertRaises(e.EligibilityError):
                e.evaluate(data, policy, facts)
        for policy in [{"max_required_years": -1}, {"max_required_years": True}, {"unknown": True}]:
            with self.subTest(policy=policy), self.assertRaises(settings.SettingsError):
                e.evaluate(listing(), policy)

    def test_evaluator_never_reads_workspace_or_clock(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")), \
             patch.object(settings, "load_settings", side_effect=AssertionError("unexpected workspace read")):
            self.check("3+ years of experience.", "eligible")


if __name__ == "__main__":
    unittest.main()
