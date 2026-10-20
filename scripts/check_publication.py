#!/usr/bin/env python3
"""Read-only publication gate. Reports contain reason counts, never input values."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import unicodedata
from urllib.parse import unquote
import zlib


# Adding a generic file requires an explicit policy review here, independently of
# .gitignore. In particular, an allowed directory is never a recursive grant.
GENERIC_FILES = frozenset({
    ".gitignore", "README.md", "CONTRIBUTING.md", "LICENSE", "master-plan.md",
    "scripts/workspace.py", "scripts/resolve_resume.py", "scripts/import_pdf.py",
    "scripts/export_dashboard.py", "scripts/check_publication.py", "scripts/workspace_config.py", "scripts/seen_index.py", "scripts/seen.sh",
    "scripts/settings.py", "settings.example.json", "docs/settings.md",
    "scripts/answers.py", "docs/answers.md",
    "scripts/extract_linkedin.js", "scripts/listing_input.py", "docs/extractor.md",
    "scripts/safety.py", "docs/safety.md",
    "scripts/scope_guard.py", ".claude/skills/apply-linkedin/SKILL.md",
    "scripts/setup_workspace.py", "docs/setup.md",
    "tests/fixtures/linkedin-easy-card.html", "tests/fixtures/linkedin-no-card.html", "tests/fixtures/linkedin-external.html",
    "dashboard/index.html", "dashboard/app.js", "dashboard/runs.js",
    "dashboard/style.css", "dashboard/README.md",
    "applications/job-input.example.json", "applications/TEMPLATE.md",
    "roadmap/README.md", "roadmap/backlog-v2.json", "roadmap/github-state.json",
    "roadmap/render.py", "roadmap/create_github.py", "docs/privacy.md",
    "docs/brand/NOTES.md", "docs/brand/build.py", "docs/brand/preview.html",
    "docs/brand/banner.svg", "docs/brand/social-preview.svg",
    "docs/brand/sprite.svg", "docs/brand/spritesheet.svg",
})
PNG_FILES = frozenset({
    "docs/brand/banner.png", "docs/brand/social-preview.png",
    "docs/brand/sprite-64.png", "docs/brand/sprite-128.png",
    "docs/brand/sprite-256.png", "docs/brand/spritesheet-512x128.png",
})
TEST_PATH = re.compile(r"tests/(?:test_[a-z0-9_]+\.py|[a-z0-9-]+\.test\.cjs)\Z")
ISSUE_PATH = re.compile(r"roadmap/issues/[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")
MAX_FILE = 16 * 1024 * 1024
MAX_MARKERS = 1024 * 1024
CANARY = re.compile(r"(?:PUBLICATION|PRIVACY|PRIVATE)[_-]CANARY[_-][A-Z0-9_-]+", re.I)
HOME_PATH = re.compile(r"(?<![A-Za-z0-9_./:-])(?:/(?:Users|home)/[^/\s\"'<>]+/|[A-Z]:[\\/]Users[\\/][^\\/\s\"'<>]+[\\/])", re.I)
EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Z0-9-]+(?:\.[A-Z0-9-]+)+)", re.I)
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{16}|-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"
    r"|https?://[^\s/:]+:[^\s/@]+@"
    r"|(?:aws_secret_access_key|api_key|access_token)\s*[=:]\s*[\"'][A-Za-z0-9/+_=.-]{16,}[\"'])",
    re.I,
)


class GateError(Exception):
    """Only static reason codes may cross the reporting boundary."""


def generic_path(name):
    """Exact files and two narrow, single-level source/document classes."""
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
        return False
    parts = PurePosixPath(name).parts
    if name.startswith("/") or any(p in {".", ".."} for p in name.split("/")):
        return False
    if "/".join(parts) != name:
        return False
    return bool(name in GENERIC_FILES or name in PNG_FILES or TEST_PATH.fullmatch(name) or ISSUE_PATH.fullmatch(name))


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold()


class Scanner:
    def __init__(self, markers=()):
        self.markers = tuple(normalized(marker) for marker in markers)
        self.findings = Counter()
        self.files = 0
        self.commits = 0

    def text(self, value, contacts=True):
        # URL and JSON escaping must not hide a literal private comparison value.
        values = [value, unquote(value)]
        for token in re.findall(r'"(?:[^"\\]|\\.)*"', value):
            try:
                values.append(json.loads(token))
            except (ValueError, RecursionError):
                pass
        values = list(dict.fromkeys(values))
        folded = "\n".join(normalized(item) for item in values)
        if any(marker in folded for marker in self.markers):
            self.findings["private_marker"] += 1
        if any(CANARY.search(item) for item in values):
            self.findings["synthetic_canary"] += 1
        if any(HOME_PATH.search(item) for item in values):
            self.findings["personal_path"] += 1
        if any(SECRET.search(item) for item in values):
            self.findings["credential"] += 1
        for item in values if contacts else ():
            for match in EMAIL.finditer(item):
                domain = match[1].lower()
                if not (match[0].lower() == "applyd@users.noreply.github.com"
                        or domain in {"example.com", "example.org", "example.net"}
                        or domain.endswith((".example", ".invalid", ".test"))):
                    self.findings["contact_value"] += 1

    def path(self, name):
        self.text(name)
        if not generic_path(name):
            self.findings["unapproved_path"] += 1
            return False
        return True

    def content(self, name, data):
        self.files += 1
        if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            self.findings["external_payload"] += 1
        # Raw bytes are scanned even for the small, explicit binary asset set.
        self.text(data.decode("utf-8", errors="replace"))
        if name in PNG_FILES:
            self.png(data)
        else:
            try:
                data.decode("utf-8")
                if b"\0" in data:
                    raise ValueError
            except (UnicodeError, ValueError):
                self.findings["unreviewable_content"] += 1

    def png(self, data):
        """Validate framing and scan compressed text; pixels need visual review."""
        try:
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError
            offset, first, ended = 8, True, False
            while offset < len(data):
                length = int.from_bytes(data[offset:offset + 4], "big")
                kind = data[offset + 4:offset + 8]
                end = offset + 12 + length
                if end > len(data) or len(kind) != 4:
                    raise ValueError
                chunk = data[offset + 8:end - 4]
                if zlib.crc32(kind + chunk) != int.from_bytes(data[end - 4:end], "big"):
                    raise ValueError
                if first and (kind != b"IHDR" or length != 13):
                    raise ValueError
                first = False
                if kind == b"tEXt":
                    self.text(chunk.decode("latin-1"))
                elif kind == b"zTXt":
                    _, compressed = chunk.split(b"\0", 1)
                    if compressed[:1] != b"\0":
                        raise ValueError
                    self.text(inflate(compressed[1:]).decode("latin-1"))
                elif kind == b"iTXt":
                    _, rest = chunk.split(b"\0", 1)
                    flag, method = rest[:2]
                    _, _, payload = rest[2:].split(b"\0", 2)
                    if flag not in (0, 1) or method != 0:
                        raise ValueError
                    self.text((inflate(payload) if flag else payload).decode("utf-8"))
                if kind == b"IEND":
                    if length or end != len(data):
                        raise ValueError
                    ended = True
                offset = end
            if not ended:
                raise ValueError
        except (ValueError, IndexError, UnicodeError, zlib.error, GateError):
            self.findings["unreviewable_content"] += 1

    def report(self):
        return {"ok": not self.findings, "files_checked": self.files,
                "commits_checked": self.commits, "findings": dict(sorted(self.findings.items()))}


def inflate(data):
    decoder = zlib.decompressobj()
    result = decoder.decompress(data, MAX_FILE + 1)
    if len(result) > MAX_FILE or not decoder.eof or decoder.unused_data:
        raise GateError("unreviewable_content")
    return result


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                    "GIT_TERMINAL_PROMPT": "0"}
        # Ignore inherited selectors: the requested repository is the boundary.
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            self.env.pop(key, None)
        top = self.git("rev-parse", "--show-toplevel").decode().strip()
        if Path(top).resolve() != self.root:
            raise GateError("repository_root")
        if self.git("rev-parse", "--is-shallow-repository").strip() != b"false":
            raise GateError("incomplete_history")
        # Reject promisor repositories before any object access. Older Git
        # versions may not implement GIT_NO_LAZY_FETCH and must not fetch data.
        if (self.git("config", "--get", "extensions.partialClone", optional=True)
                or self.git("config", "--get-regexp", r"^remote\..*\.promisor$", optional=True)):
            raise GateError("incomplete_history")

    def git(self, *args, optional=False):
        result = subprocess.run(["git", "--no-replace-objects", "-C", str(self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env)
        if result.returncode:
            if optional:
                return b""
            raise GateError("git_unavailable")
        return result.stdout

    def blob(self, oid):
        if not re.fullmatch(rb"[a-f0-9]{40}|[a-f0-9]{64}", oid):
            raise GateError("invalid_git_entry")
        if self.git("cat-file", "-t", oid.decode()).strip() != b"blob":
            raise GateError("invalid_git_entry")
        size = int(self.git("cat-file", "-s", oid.decode()))
        if size > MAX_FILE:
            raise GateError("oversized_content")
        return self.git("cat-file", "blob", oid.decode())


def git_entries(repo, scanner, entries, seen, index=False):
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        header, raw_name = entry.split(b"\t", 1)
        name = raw_name.decode("utf-8", errors="surrogateescape")
        mode, middle, last = header.split(b" ")
        oid = middle if index else last
        approved = scanner.path(name)
        if (index and last != b"0") or mode not in (b"100644", b"100755"):
            scanner.findings["unsafe_git_entry"] += 1
            continue
        if not approved:
            continue  # Never read private blobs, even to diagnose a violation.
        key = (name, oid)
        if key not in seen:
            seen.add(key)
            try:
                scanner.content(name, repo.blob(oid))
            except GateError as error:
                scanner.findings[str(error)] += 1


def history(repo, scanner, seen):
    refs = repo.git("for-each-ref", "--format=%(objectname) %(objecttype) %(refname)")
    tags_seen = set()
    for line in refs.splitlines():
        oid, kind, name = line.split(b" ", 2)
        scanner.text(name.decode("utf-8", errors="replace"))
        while kind == b"tag" and oid not in tags_seen:
            tags_seen.add(oid)
            tag = repo.git("cat-file", "tag", oid.decode())
            header, _, message = tag.partition(b"\n\n")
            scanner.text(header.decode("utf-8", errors="replace"), contacts=False)
            scanner.text(message.decode("utf-8", errors="replace"))
            fields = dict(line.split(b" ", 1) for line in header.splitlines() if b" " in line)
            oid, kind = fields.get(b"object", b""), fields.get(b"type", b"")
        if kind != b"commit" and not (kind == b"tag" and oid in tags_seen):
            scanner.findings["unsafe_history_ref"] += 1
    args = ["rev-list", "--all"]
    if repo.git("rev-parse", "--verify", "HEAD", optional=True):
        args.append("HEAD")  # Include detached HEAD as well as branches/tags.
    for oid in repo.git(*args).splitlines():
        scanner.commits += 1
        header, _, message = repo.git("cat-file", "commit", oid.decode()).partition(b"\n\n")
        scanner.text(header.decode("utf-8", errors="replace"), contacts=False)
        scanner.text(message.decode("utf-8", errors="replace"))
        git_entries(repo, scanner, repo.git("ls-tree", "-rz", "--full-tree", oid.decode()), seen)


def read_regular(root, name, limit=MAX_FILE):
    """Open beneath root without following even an intermediate symlink."""
    parts = PurePosixPath(name).parts
    if not parts or any(p in {"", ".", ".."} for p in name.split("/")) or name.startswith("/"):
        raise GateError("invalid_manifest")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise GateError("unsafe_manifest_entry")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise GateError("oversized_content")
            return data
    finally:
        os.close(fd)


def manifest(repo, scanner, manifest_file):
    data = load_json(manifest_file)
    if not isinstance(data, dict) or set(data) != {"version", "files"} or type(data["version"]) is not int or data["version"] != 1:
        raise GateError("invalid_manifest")
    if not isinstance(data["files"], list) or not data["files"]:
        raise GateError("empty_publication")
    seen = set()
    for item in data["files"]:
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str) or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise GateError("invalid_manifest")
        name = item["path"]
        if name in seen:
            raise GateError("invalid_manifest")
        seen.add(name)
        if not scanner.path(name):
            continue
        try:
            content = read_regular(repo.root, name)
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                scanner.findings["manifest_digest"] += 1
            scanner.content(name, content)
        except (OSError, GateError) as error:
            scanner.findings[str(error) if isinstance(error, GateError) else "unsafe_manifest_entry"] += 1


def load_json(path):
    # Reject duplicate keys instead of silently selecting one manifest meaning.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GateError("invalid_manifest")
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise GateError("invalid_manifest")
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise GateError("oversized_content")
    return json.loads(data, object_pairs_hook=pairs)


def private_markers(repo, path):
    if path is None:
        return ()
    resolved = Path(path).resolve(strict=True)
    common = Path(os.fsdecode(repo.git("rev-parse", "--git-common-dir").strip()))
    if not common.is_absolute():
        common = repo.root / common
    if (resolved.is_relative_to(repo.root) or resolved.is_relative_to(common.resolve())
            or any(parent.samefile(boundary) for parent in resolved.parents
                   for boundary in (repo.root, common.resolve()))):
        raise GateError("markers_not_external")
    fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise GateError("invalid_markers")
        content = stream.read(MAX_MARKERS + 1)
    if len(content) > MAX_MARKERS:
        raise GateError("invalid_markers")
    markers = tuple(line.strip() for line in content.decode("utf-8").splitlines() if line.strip())
    if not markers or any("\0" in marker for marker in markers):
        raise GateError("invalid_markers")
    return markers


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise GateError("invalid_arguments")


def main(argv=None):
    scanner = Scanner()
    try:
        parser = QuietParser(description=__doc__)
        parser.add_argument("--repo", default=".", help="Git repository root (default: current directory)")
        parser.add_argument("--manifest", help="Version 1 JSON file with path and SHA-256 entries; otherwise check index")
        parser.add_argument("--markers-file", help="External UTF-8 literal private markers, one per line; never echoed")
        args = parser.parse_args(argv)
        repo = Repository(args.repo)
        scanner = Scanner(private_markers(repo, args.markers_file))
        seen = set()
        if args.manifest:
            manifest(repo, scanner, args.manifest)
        else:
            entries = repo.git("ls-files", "--stage", "-z")
            if not entries:
                scanner.findings["empty_publication"] += 1
            git_entries(repo, scanner, entries, seen, index=True)
        history(repo, scanner, seen)
    except GateError as error:
        scanner.findings[str(error)] += 1
    except (OSError, ValueError, UnicodeError, RecursionError):
        scanner.findings["unreadable_input"] += 1
    print(json.dumps(scanner.report(), sort_keys=True))
    return 0 if not scanner.findings else 1


if __name__ == "__main__":
    sys.exit(main())
