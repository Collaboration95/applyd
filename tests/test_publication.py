"""Publication regressions. All Git mutations and data belong to temporary fixtures."""

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publication", SOURCE / "scripts/check_publication.py")
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)
sys.path.insert(0, str(SOURCE / "scripts"))
from resolve_resume import resolve


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Example Contributor")
        self.git("config", "user.email", "developer@example.invalid")
        self.write(".gitignore", (SOURCE / ".gitignore").read_bytes())
        self.write("README.md", "Generic synthetic project.\n")
        self.git("add", ".gitignore", "README.md")

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(self.root), *args], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            self.fail("Synthetic Git fixture operation failed")
        return result.stdout

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def canary(self):
        return "PUBLICATION" + "_CANARY_" + "SYNTHETIC_742"

    def check(self, *args, root=None):
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            status = publication.main(["--repo", str(root or self.root), *map(str, args)])
        self.assertEqual(error.getvalue(), "")
        self.assertNotIn(self.canary(), output.getvalue())
        self.assertNotIn(self.canary().casefold(), output.getvalue().casefold())
        self.assertNotIn(str(self.base), output.getvalue())
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0 if report["ok"] else 1)
        return report

    def blocked(self, code, *args, **kwargs):
        report = self.check(*args, **kwargs)
        self.assertFalse(report["ok"])
        self.assertIn(code, report["findings"])
        return report

    def release_manifest(self, names=("README.md",), **changes):
        data = {"version": 1, "files": [
            {"path": name, "sha256": hashlib.sha256((self.root / name).read_bytes()).hexdigest()}
            for name in names]}
        data.update(changes)
        path = self.base / "release.json"
        path.write_text(json.dumps(data))
        return path

    def commit(self, message="Synthetic baseline"):
        self.git("commit", "-qm", message)

    def test_generic_index_and_full_history_pass(self):
        for name in ("scripts/check_publication.py", "tests/test_publication.py", "docs/privacy.md",
                     "roadmap/issues/example-01-synthetic.md", "dashboard/app.js"):
            self.write(name, "# Generic synthetic fixture\n")
        self.git("add", ".")
        self.commit()
        report = self.check()
        self.assertTrue(report["ok"])
        self.assertEqual(report["commits_checked"], 1)
        self.assertEqual(report["files_checked"], 7)

    def test_empty_index_is_not_a_successful_publication(self):
        self.git("read-tree", "--empty")
        self.blocked("empty_publication")

    def test_normal_add_cannot_stage_private_workspace_data(self):
        private = ("profile/answers.json", "resumes/synthetic-only.pdf", "projects/catalog.json",
                   "sources/synthetic-original.pdf", "tmp/synthetic.txt", "AGENTS.md",
                   "applications/records/synthetic/record.json", "applications/log.csv",
                   "dashboard/data/applications.json", "dashboard/data.js", "private/synthetic.txt",
                   "credentials.json", ".env", "screenshots/synthetic.png",
                   "scripts/private-export.json", "scripts/tmp/output.py",
                   "tests/fixtures/profile.json", "docs/private-record.md")
        for name in private:
            self.write(name, self.canary())
        self.git("add", ".")
        staged = set(self.git("ls-files", "-z").decode().split("\0"))
        self.assertFalse(any(name in staged for name in private))
        ignored = set(self.git("check-ignore", "--", *private).decode().splitlines())
        self.assertTrue(all(name in ignored for name in private))
        self.assertTrue(self.check()["ok"])

    def test_baseline_tracks_only_generic_paths(self):
        for name in ("applications/job-input.example.json", "applications/TEMPLATE.md",
                     "tests/test_workflow.py", "docs/privacy.md"):
            self.write(name, "Synthetic fixture\n")
        self.write("profile/answers.json", self.canary())
        self.git("add", ".")
        self.commit()
        names = self.git("ls-files", "-z").decode().strip("\0").split("\0")
        self.assertTrue(all(publication.generic_path(name) for name in names))
        self.assertTrue(self.check()["ok"])

    def test_private_paths_are_rejected_without_reading_their_blobs(self):
        blob = self.git("rev-parse", ":README.md").strip()
        # update-index is used only in this synthetic repository to simulate a bypass.
        self.git("update-index", "--add", "--cacheinfo", "100644", blob.decode(), "profile/synthetic.json")
        original = publication.Repository.blob
        def guarded(repo, oid):
            return original(repo, oid)
        with mock.patch.object(publication.Repository, "blob", autospec=True, side_effect=guarded) as read:
            self.blocked("unapproved_path")
        # Only the two approved index blobs are opened, not the forbidden entry.
        self.assertEqual(read.call_count, 2)

    def test_broadly_ignored_directory_allowances_do_not_expand_checker_policy(self):
        # Deliberately weak ignore rules prove that the gate is independent of
        # staging containment, even after the real repository tightens its rules.
        self.write(".gitignore", "/*\n!/.gitignore\n!/README.md\n!/scripts/\n!/tests/\n!/docs/\n")
        names = ("scripts/private-export.json", "scripts/tmp/output.py", "tests/fixtures/profile.json",
                 "tests/data.js", "docs/private-record.md", "docs/generated/output.json",
                 "roadmap/private.json", "dashboard/data.js", "applications/log.csv")
        for name in names:
            self.assertFalse(publication.generic_path(name))
        for name in ("scripts/private-export.json", "tests/fixtures/profile.json", "docs/private-record.md"):
            self.write(name, "Synthetic data")
        self.git("add", ".")
        self.blocked("unapproved_path")

    def test_canary_in_staged_content_is_redacted(self):
        self.write("README.md", self.canary())
        self.git("add", "README.md")
        self.blocked("synthetic_canary")

    def test_index_is_checked_even_if_working_copy_is_clean(self):
        self.write("README.md", self.canary())
        self.git("add", "README.md")
        self.write("README.md", "Clean working copy")
        self.blocked("synthetic_canary")

    def test_unstaged_files_are_not_misrepresented_as_index_content(self):
        self.write("README.md", self.canary())
        self.assertTrue(self.check()["ok"])
        self.blocked("synthetic_canary", "--manifest", self.release_manifest())

    def test_canary_in_unapproved_filename_is_redacted(self):
        name = "docs/" + self.canary().lower() + ".txt"
        oid = self.git("rev-parse", ":README.md").strip().decode()
        self.git("update-index", "--add", "--cacheinfo", "100644", oid, name)
        self.blocked("synthetic_canary")

    def test_deleted_historical_content_still_blocks(self):
        self.write("README.md", self.canary())
        self.git("add", "README.md")
        self.commit()
        self.write("README.md", "Generic replacement")
        self.git("add", "README.md")
        self.commit("Synthetic replacement")
        self.blocked("synthetic_canary")

    def test_deleted_historical_private_path_still_blocks(self):
        oid = self.git("rev-parse", ":README.md").strip().decode()
        self.git("update-index", "--add", "--cacheinfo", "100644", oid, "resumes/synthetic.pdf")
        self.commit()
        self.git("update-index", "--force-remove", "resumes/synthetic.pdf")
        self.commit("Synthetic removal")
        self.blocked("unapproved_path")

    def test_commit_message_and_annotated_tag_are_scanned(self):
        self.commit(self.canary())
        self.git("tag", "-a", "synthetic-tag", "-m", self.canary())
        report = self.blocked("synthetic_canary")
        self.assertGreaterEqual(report["findings"]["synthetic_canary"], 2)

    def test_nested_tag_metadata_is_scanned(self):
        self.commit()
        self.git("tag", "-a", "synthetic-inner", "-m", self.canary())
        self.git("tag", "-a", "synthetic-outer", "synthetic-inner", "-m", "Synthetic outer")
        self.git("tag", "-d", "synthetic-inner")
        self.blocked("synthetic_canary")

    def test_other_branches_and_detached_head_are_scanned(self):
        self.commit()
        self.git("checkout", "-qb", "synthetic-other")
        self.write("README.md", self.canary())
        self.git("add", "README.md")
        self.commit()
        self.git("checkout", "--detach", "HEAD~1")
        self.blocked("synthetic_canary")

    def test_dangling_detached_head_is_scanned(self):
        self.commit()
        self.git("checkout", "--detach")
        self.write("README.md", self.canary())
        self.git("add", "README.md")
        self.commit()
        self.blocked("synthetic_canary")

    def test_refs_to_noncommit_objects_block(self):
        oid = self.git("rev-parse", ":README.md").strip().decode()
        self.git("update-ref", "refs/tags/synthetic-blob", oid)
        self.blocked("unsafe_history_ref")

    def test_external_marker_file_is_used_without_echo(self):
        marker = "Synthetic" + "SensitiveName742"
        markers = self.base / "private-markers.txt"
        markers.write_text(marker + "\n")
        self.write("README.md", marker.swapcase())
        self.git("add", "README.md")
        report = self.blocked("private_marker", "--markers-file", markers)
        self.assertNotIn(marker, json.dumps(report))

    def test_marker_matches_filenames_json_and_url_escapes(self):
        marker = "Synthetic" + "SensitiveName742"
        scanner = publication.Scanner([marker])
        scanner.path("tests/test_" + marker + ".py")
        scanner.text('"' + "".join("\\u%04x" % ord(c) for c in marker) + '"')
        scanner.text("".join("%%%02x" % ord(c) for c in marker))
        self.assertEqual(scanner.findings["private_marker"], 3)

    def test_internal_or_empty_marker_file_blocks(self):
        markers = self.write("scripts/markers.txt", "Synthetic secret")
        self.blocked("markers_not_external", "--markers-file", markers)
        external = self.base / "empty.txt"
        external.write_text("\n")
        self.blocked("invalid_markers", "--markers-file", external)

    def test_manifest_requires_matching_hash_and_explicit_nonempty_inventory(self):
        manifest = self.release_manifest()
        self.assertTrue(self.check("--manifest", manifest)["ok"])
        self.write("README.md", "Changed fixture")
        self.blocked("manifest_digest", "--manifest", manifest)
        self.blocked("empty_publication", "--manifest", self.release_manifest(files=[]))

    def test_manifest_cannot_suppress_history_check(self):
        self.commit(self.canary())
        self.blocked("synthetic_canary", "--manifest", self.release_manifest())

    def test_malformed_duplicate_or_escaping_manifest_blocks(self):
        for data in (
            '{"version":1,"version":1,"files":[]}',
            '{"version":true,"files":[]}',
            '{"version":1,"files":[{"path":"../outside","sha256":"' + "0" * 64 + '"}]}',
            '{"version":1,"files":[{"path":"README.md","sha256":"invalid"}]}',
        ):
            manifest = self.base / "invalid.json"
            manifest.write_text(data)
            self.assertFalse(self.check("--manifest", manifest)["ok"])
        manifest = self.release_manifest()
        data = json.loads(manifest.read_text())
        data["files"] *= 2
        manifest.write_text(json.dumps(data))
        self.blocked("invalid_manifest", "--manifest", manifest)

    def test_noncanonical_paths_never_enter_generic_classes(self):
        for name in ("/README.md", "./README.md", "docs/../README.md", "docs//privacy.md",
                     "tests\\test_example.py", "tests/test_example.py\n", "tests/test_example.py/child"):
            self.assertFalse(publication.generic_path(name))

    def test_index_symlink_blocks_without_following(self):
        (self.root / "README.md").unlink()
        os.symlink(self.base / "missing-private-file", self.root / "README.md")
        self.git("add", "README.md")
        self.blocked("unsafe_git_entry")

    def test_manifest_rejects_file_and_parent_directory_symlinks(self):
        manifest = self.release_manifest()
        (self.root / "README.md").unlink()
        os.symlink(self.base / "missing", self.root / "README.md")
        self.blocked("unsafe_manifest_entry", "--manifest", manifest)
        external = self.base / "external"
        external.mkdir()
        (external / "privacy.md").write_text("Synthetic")
        os.symlink(external, self.root / "docs")
        manifest = self.release_manifest(("docs/privacy.md",))
        self.blocked("unsafe_manifest_entry", "--manifest", manifest)

    def test_submodule_index_entry_blocks(self):
        self.commit()
        oid = self.git("rev-parse", "HEAD").strip().decode()
        self.git("update-index", "--add", "--cacheinfo", "160000", oid, "docs/privacy.md")
        self.blocked("unsafe_git_entry")

    def test_unmerged_index_blocks(self):
        oid = self.git("rev-parse", ":README.md").strip().decode()
        self.git("update-index", "--force-remove", "README.md")
        result = subprocess.run(["git", "-C", str(self.root), "update-index", "--index-info"],
                                input=f"100644 {oid} 1\tREADME.md\n".encode(), capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.blocked("unsafe_git_entry")

    def test_unsupported_binary_and_oversized_content_block(self):
        self.write("README.md", b"\0synthetic")
        self.git("add", "README.md")
        self.blocked("unreviewable_content")
        with mock.patch.object(publication, "MAX_FILE", 4):
            self.blocked("oversized_content")

    def test_external_payload_pointer_is_not_a_content_audit(self):
        self.write("README.md", "version https://git-lfs.github.com/spec/v1\n" +
                   "oid sha256:" + "a" * 64 + "\nsize 12\n")
        self.git("add", "README.md")
        self.blocked("external_payload")

    def test_png_compressed_metadata_is_scanned(self):
        def chunk(kind, value):
            return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        data += chunk(b"zTXt", b"note\0\0" + zlib.compress(self.canary().encode()))
        data += chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b"")
        self.write("docs/brand/banner.png", data)
        self.git("add", ".")
        self.blocked("synthetic_canary")

    def test_png_latin1_metadata_and_malformed_assets_block(self):
        def chunk(kind, value):
            return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
        marker = "Synthetic" + chr(233)
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        data += chunk(b"tEXt", b"note\0" + marker.encode("latin-1")) + chunk(b"IEND", b"")
        scanner = publication.Scanner([marker])
        scanner.content("docs/brand/banner.png", data)
        self.assertIn("private_marker", scanner.findings)
        scanner.content("docs/brand/banner.png", data + b"unexpected trailer")
        self.assertIn("unreviewable_content", scanner.findings)

    def test_personal_paths_contacts_and_credentials_are_redacted(self):
        values = ("/" + "Users/synthetic/private.txt", "C:" + "\\Users\\synthetic\\private.txt",
                  "synthetic@" + "private-mail.example.edu", "ghp_" + "a" * 30)
        for value in values:
            self.write("README.md", value)
            self.git("add", "README.md")
            self.assertFalse(self.check()["ok"])

    def test_shallow_history_is_incomplete(self):
        self.commit()
        (self.root / ".git/shallow").write_bytes(self.git("rev-parse", "HEAD"))
        self.blocked("incomplete_history")

    def test_partial_clone_is_rejected_before_object_access(self):
        self.git("config", "remote.origin.promisor", "true")
        with mock.patch.object(publication.Repository, "blob") as read:
            self.blocked("incomplete_history")
        read.assert_not_called()

    def test_replace_refs_cannot_hide_original_commit(self):
        self.commit(self.canary())
        original = self.git("rev-parse", "HEAD").strip().decode()
        self.write("README.md", "Synthetic replacement")
        self.git("add", "README.md")
        self.commit("Synthetic clean replacement")
        replacement = self.git("rev-parse", "HEAD").strip().decode()
        self.git("replace", original, replacement)
        self.blocked("synthetic_canary")

    def test_git_environment_cannot_redirect_repository_or_index(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.base / "missing"),
                                          "GIT_INDEX_FILE": str(self.base / "missing-index")}):
            self.assertTrue(self.check()["ok"])

    def test_errors_do_not_echo_arguments_or_paths(self):
        self.blocked("invalid_arguments", "--unknown-" + self.canary())
        self.blocked("unreadable_input", "--markers-file", self.base / self.canary())

    def test_legacy_synthetic_files_resolve_and_bytes_remain_unchanged(self):
        now = datetime.now(timezone.utc)
        resume = self.write("resumes/synthetic.pdf", b"synthetic immutable PDF bytes")
        registry = {"releases": [{
            "id": "synthetic-v1", "scope": "synthetic", "purpose": "application", "status": "active",
            "path": "resumes/synthetic.pdf", "sha256": hashlib.sha256(resume.read_bytes()).hexdigest(),
            "released_at": (now - timedelta(days=1)).isoformat(),
            "effective_from": (now - timedelta(days=1)).isoformat(),
            "review_due_at": (now + timedelta(days=1)).isoformat(), "valid_until": None,
            "source_master_id": "synthetic-master", "selected_projects": [],
        }]}
        master = self.write("resumes/synthetic-master.md", "Synthetic master")
        registry["releases"].append({**registry["releases"][0], "id": "synthetic-master",
                                     "scope": "master", "purpose": "reference",
                                     "path": "resumes/synthetic-master.md", "source_master_id": None,
                                     "sha256": hashlib.sha256(master.read_bytes()).hexdigest()})
        self.write("resumes/registry.json", json.dumps(registry))
        self.write("projects/catalog.json", '{"projects": []}')
        for name in ("profile/answers.json", "sources/synthetic-original.pdf",
                     "applications/records/synthetic/record.json", "dashboard/data.js"):
            self.write(name, "Synthetic immutable data")
        private = [path for path in self.root.rglob("*") if path.is_file() and ".git" not in path.parts]
        before = {path: path.read_bytes() for path in private}
        self.assertEqual(resolve(registry, "synthetic", "application", now, self.root)["id"], "synthetic-v1")
        self.git("add", ".")
        self.assertTrue(self.check()["ok"])
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))


if __name__ == "__main__":
    unittest.main()
