"""Behavioural checks use temporary workspaces; no real application is submitted."""

import argparse
import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workspace as w
from resolve_resume import resolve


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)
        w.atomic_json(self.root / "projects/catalog.json", {"projects": []})
        self.master = self.add_release("master-v1", "master", "reference", content="# Person\n\n## Projects\n\nOld\n\n## Certifications\n\nAWS\n")
        self.application = self.add_release("backend-v1", "backend", "application", master="master-v1")
        self.registry = {"releases": [self.master, self.application]}
        self.save_registry()

    def add_release(self, identity, scope, purpose, master=None, content="Fixture resume"):
        path = self.root / f"resumes/{identity}{'.md' if purpose == 'reference' else '.pdf'}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return {"id": identity, "scope": scope, "purpose": purpose, "status": "active",
                "path": str(path.relative_to(self.root)), "sha256": w.digest(path),
                "released_at": (self.now - timedelta(days=2)).isoformat(),
                "effective_from": (self.now - timedelta(days=2)).isoformat(),
                "review_due_at": (self.now + timedelta(days=30)).isoformat(),
                "valid_until": None, "source_master_id": master, "selected_projects": []}

    def save_registry(self):
        w.atomic_json(self.root / "resumes/registry.json", self.registry)

    def job(self, **changes):
        return {"company": "Example", "role": "Engineer", "location": "Singapore",
                "platform": "linkedin", "platform_job_id": "123456", "listing_url": "https://www.linkedin.com/jobs/view/123456?trk=search",
                "job_description": "Full role text\nMust retain every line.", **changes}

    def capture(self):
        return w.capture(self.root, self.job(), self.now)["application_id"]

    def event(self, identity, status, **extra):
        args = argparse.Namespace(id=identity, status=status, note="", resume_scope=None, answers=None, evidence=None)
        for key, value in extra.items():
            setattr(args, key, value)
        return w.event(self.root, args, self.now)

    def ready(self):
        identity = self.capture()
        self.event(identity, "preparing")
        answers = self.root / "answers.json"
        w.atomic_json(answers, [{"question": "Sponsorship required?", "answer": "Yes", "source_answer_id": "sponsorship_required"}])
        self.event(identity, "ready", resume_scope="backend", answers=answers)
        return identity

    def record(self, identity):
        return w.read(self.root / "applications/records" / identity / "record.json")

    def evidence(self):
        path = self.root / "confirmation.txt"
        path.write_text("Observed application confirmation (test fixture)")
        return path

    def test_expired_latest_release_never_falls_back(self):
        replacement = self.add_release("backend-v2", "backend", "application", master="master-v1")
        replacement.update(effective_from=(self.now - timedelta(days=1)).isoformat(), valid_until=self.now.isoformat())
        self.registry["releases"].append(replacement)
        with self.assertRaisesRegex(ValueError, "valid_until"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_future_release_is_not_yet_a_replacement(self):
        replacement = self.add_release("backend-v2", "backend", "application", master="master-v1")
        replacement["effective_from"] = (self.now + timedelta(days=1)).isoformat()
        self.registry["releases"].append(replacement)
        self.assertEqual(resolve(self.registry, "backend", "application", self.now, self.root)["id"], "backend-v1")

    def test_draft_does_not_replace_a_release(self):
        draft = {**self.application, "id": "draft", "status": "draft", "released_at": None}
        self.registry["releases"].append(draft)
        self.assertEqual(resolve(self.registry, "backend", "application", self.now, self.root)["id"], "backend-v1")

    def test_modified_file_is_rejected(self):
        (self.root / self.application["path"]).write_text("Changed after release")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_replaced_master_invalidates_variant(self):
        replacement = self.add_release("master-v2", "master", "reference")
        replacement["effective_from"] = (self.now - timedelta(days=1)).isoformat()
        self.registry["releases"].append(replacement)
        with self.assertRaisesRegex(ValueError, "older master"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_overdue_review_blocks_use(self):
        self.application["review_due_at"] = self.now.isoformat()
        with self.assertRaisesRegex(ValueError, "review_due_at"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_missing_replacement_blocks_instead_of_guessing(self):
        self.application["superseded_by"] = "missing-v2"
        with self.assertRaisesRegex(ValueError, "Missing replacement"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_project_replacement_invalidates_variant(self):
        project = {"id": "project", "updated_at": self.now.isoformat(), "bullets": [{"id": "bullet", "text": "Original resume bullet"}]}
        self.application["selected_projects"] = [{"id": "project", "content_hash": w.project_fingerprint(project), "bullet_ids": ["bullet"]}]
        project["bullets"][0]["text"] = "Updated resume bullet"
        w.atomic_json(self.root / "projects/catalog.json", {"projects": [project]})
        with self.assertRaisesRegex(ValueError, "replacement"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_draft_assembly_is_idempotent(self):
        w.atomic_json(self.root / "resumes/presets.json", {"sample": {"projects": [], "bullets_per_project": 2}})
        first = w.draft(self.root, "sample", self.now)
        manifest = Path(first["manifest"]).read_bytes()
        second = w.draft(self.root, "sample", self.now + timedelta(seconds=10))
        self.assertEqual(first, second)
        self.assertEqual(manifest, Path(second["manifest"]).read_bytes())

    def test_capture_deduplicates_tracking_url_variants(self):
        first = w.capture(self.root, self.job(), self.now)
        second = w.capture(self.root, self.job(listing_url="https://sg.linkedin.com/jobs/view/software-engineer-123456?trk=other"), self.now)
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["application_id"], second["application_id"])
        self.assertEqual(len(w.records(self.root)), 1)

    def test_capture_retains_complete_description(self):
        identity = self.capture()
        record = self.record(identity)
        self.assertEqual((self.root / record["job_description_path"]).read_text(), self.job()["job_description"])

    def test_possible_cross_post_is_flagged(self):
        self.capture()
        with self.assertRaisesRegex(ValueError, "cross-post"):
            w.capture(self.root, self.job(listing_url="https://example.org/job/other", platform="employer", platform_job_id="other"), self.now)

    def test_preflight_uses_exact_saved_resume_and_answers(self):
        identity = self.ready()
        record = self.record(identity)
        self.assertTrue(w.preflight(self.root, record, self.now)["ready"])
        (self.root / record["prepared"]["answers_path"]).write_text("[]")
        with self.assertRaisesRegex(ValueError, "snapshot was modified"):
            w.preflight(self.root, record, self.now)

    def test_submitted_requires_confirmation(self):
        identity = self.ready()
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.event(identity, "submitted")
        self.assertEqual(self.record(identity)["status"], "ready")

    def test_uncertain_submission_blocks_retries(self):
        identity = self.ready()
        self.event(identity, "submission_uncertain")
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.event(identity, "ready")
        with self.assertRaisesRegex(ValueError, "reconciliation evidence"):
            self.event(identity, "failed")

    def test_success_logging_is_idempotent(self):
        identity = self.ready()
        self.event(identity, "submitted", evidence=self.evidence())
        self.event(identity, "submitted", evidence=self.evidence())
        self.assertEqual(len(self.record(identity)["submissions"]), 1)

    def test_confirmed_historical_submission_is_not_lost_after_expiry(self):
        identity = self.ready()
        self.application["review_due_at"] = self.now.isoformat()
        self.save_registry()
        with self.assertRaisesRegex(ValueError, "review_due_at"):
            w.preflight(self.root, self.record(identity), self.now)
        self.event(identity, "submitted", evidence=self.evidence())
        saved = self.record(identity)["submissions"][0]
        self.assertEqual(saved["id"], "backend-v1")
        self.assertIn("review_due_at", saved["recording_warning"])

    def test_csv_escapes_formula_like_employer_names(self):
        w.capture(self.root, self.job(company="=untrusted()"), self.now)
        with (self.root / "applications/log.csv").open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["company"], "'=untrusted()")

    def test_path_escape_in_release_is_rejected(self):
        self.application["path"] = "../outside.pdf"
        with self.assertRaisesRegex(ValueError, "inside the workspace"):
            resolve(self.registry, "backend", "application", self.now, self.root)

    def test_release_replaces_master_without_mutating_old_file(self):
        old_bytes = (self.root / self.master["path"]).read_bytes()
        source = self.root / "new-master.md"
        source.write_text("# Updated source master")
        args = argparse.Namespace(id="master-v2", scope="master", purpose="reference", file=source,
                                  review_days=30, manifest=None, note="Update source facts")
        w.release(self.root, args, self.now)
        registry = w.read(self.root / "resumes/registry.json")
        self.assertEqual(resolve(registry, "master", "reference", self.now, self.root)["id"], "master-v2")
        self.assertEqual((self.root / self.master["path"]).read_bytes(), old_bytes)
        self.assertEqual(registry["releases"][0]["superseded_by"], "master-v2")

    def test_release_rejects_a_file_unrelated_to_its_draft(self):
        w.atomic_json(self.root / "resumes/presets.json", {"sample": {"projects": [], "bullets_per_project": 2}})
        built = w.draft(self.root, "sample", self.now)
        unrelated = self.root / "unrelated.md"
        unrelated.write_text("# A different resume")
        args = argparse.Namespace(id="other-v1", scope="other", purpose="reference", file=unrelated,
                                  review_days=30, manifest=Path(built["manifest"]), note="Fixture mismatch")
        with self.assertRaisesRegex(ValueError, "differs from the recorded draft"):
            w.release(self.root, args, self.now)

    def test_release_rejects_a_stale_draft(self):
        w.atomic_json(self.root / "resumes/presets.json", {"sample": {"projects": [], "bullets_per_project": 2}})
        built = w.draft(self.root, "sample", self.now)
        replacement = self.add_release("master-v2", "master", "reference")
        replacement["effective_from"] = (self.now - timedelta(days=1)).isoformat()
        self.registry["releases"].append(replacement)
        self.save_registry()
        args = argparse.Namespace(id="other-v1", scope="other", purpose="reference", file=Path(built["draft"]),
                                  review_days=30, manifest=Path(built["manifest"]), note="Stale fixture")
        with self.assertRaisesRegex(ValueError, "older master"):
            w.release(self.root, args, self.now)


if __name__ == "__main__":
    unittest.main()
