# Publication privacy gate

Run from the repository root before publishing code or a release:

```sh
python3 scripts/check_publication.py
python3 -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

The checker is read-only, uses Python's standard library and local Git, and
performs no network or employer actions. It returns 0 on success and 1 on any
finding or incomplete check. Its JSON report contains only boolean status,
counts and fixed reason codes. It never prints matched text, paths, filenames,
markers, Git diagnostics or input errors. Do not add verbose match logging.

## What is checked

The default input is the complete Git index, including unchanged tracked files.
Checks read indexed blobs, so replacing a staged leak with a clean working copy
does not hide it. Unstaged changes are outside this mode's scope. An empty index
fails rather than claiming that a release was audited.

Every run also checks all reachable commits from every local ref and HEAD,
including detached HEAD, historical tree paths, deleted or replaced generic
blobs, commit messages, annotated tags and ref names. Commit/tag headers undergo
marker, path and credential checks; ordinary Git authorship email addresses are
not treated as application contact values. Reflogs, unreachable objects, remote
objects absent locally and a hosting service's artifacts are
outside this local audit. Use a complete local checkout with all publication
refs present. Shallow/partial history, unavailable objects, Git LFS pointers, unmerged entries, symlinks,
submodules and refs pointing to noncommit payloads fail. Replacement objects are
disabled; inherited Git repository/index selectors cannot redirect the audit.

`GENERIC_FILES` and `PNG_FILES` in the checker are explicit audited inventories.
The only pattern classes are single-level Python test files, Node test files and
roadmap issue Markdown. New entries require a content review; `.gitignore`
permission alone never grants publication permission. The checker does not
recursively allow `scripts/`, `tests/` or `docs/`. Private exports, generated data,
arbitrary JSON fixtures, local guidance and nested temporary directories fail.
Only the two named generic application templates are allowed under
`applications/`; generated dashboard files are excluded. Forbidden paths fail
without opening their blobs or reading their working-tree contents.

Text must be valid UTF-8 without NUL bytes. The explicit brand PNG inventory is
scanned for raw markers and compressed textual metadata, with framing and CRC
validation. PNG pixels and SVG appearance still require a visual review; the
checker does not perform OCR or establish that an image depicts fictional data.
Other binary files and oversized content fail. Built-in rules reject synthetic
canaries, home-directory paths, nonexample contact email values and common
credential formats. A canary is `PUBLICATION_CANARY_<token>` (or the corresponding
privacy/private prefix), with an alphanumeric token. Tests construct these
strings dynamically so the test source itself remains publishable.

## Private comparison markers

Generic patterns cannot identify every real name, artifact filename or private
value. Supply a local UTF-8 file outside the repository containing one literal
private comparison marker per nonblank line:

```sh
python3 scripts/check_publication.py --markers-file "$PRIVATE_MARKERS_FILE"
```

Create and retain this file privately, outside Git and shared logs. Do not commit
it, pass private marker values as command arguments, or generate it by exporting
records into the public checkout. There is no automatic profile/resume scan.
An explicitly supplied marker file must be nonempty and outside both the
checkout and its common Git directory, including after symlink resolution.
Markers apply to candidate paths, content and history. Comparison ignores case
and Unicode compatibility differences; URL-encoded and JSON-escaped text is
also checked. Blank lines are ignored; other lines are literal, not comments or
regular expressions. Choose specific markers to avoid accidental common-word
matches. A run without this file provides only the built-in checks and must not
be presented as proof that all identifying values were removed.

## Release manifests

For a release assembled from working-tree files, supply a UTF-8 JSON manifest:

```json
{
  "version": 1,
  "files": [
    {"path": "README.md", "sha256": "<64 lowercase hexadecimal characters>"}
  ]
}
```

```sh
python3 scripts/check_publication.py --manifest "$RELEASE_MANIFEST" --markers-file "$PRIVATE_MARKERS_FILE"
```

Paths are canonical, case-sensitive and relative to the selected repository root
(`--repo` defaults to the current directory). Every entry must name an approved
regular file and match its SHA-256 digest. Absolute paths, traversal, duplicate
entries/keys, unknown schema fields, empty manifests and symlinks in any path
component fail. No recursive directory expansion occurs. Private files outside
this exact inventory are not read. History is still mandatory in manifest mode.
The packaging step must include exactly these paths and verified bytes; an
unlisted artifact or a post-check modification has not passed this gate. This
tool checks a release inventory, not an arbitrary archive or a standalone export
without Git history.

## Integration and containment

Keep `.gitignore` deny-by-default and never force-add private data. Recursive
`scripts/`, `tests/`, `docs/` or `roadmap/` exceptions are weaker than this
checker's inventory. Use individually audited files or narrow source classes
with explicit generated-data exclusions. In
particular, ordinary `git add` must not admit private output placed beneath a
code/documentation directory. The checker blocks these entries independently,
but cannot make Git's staging rules stricter.

Run the index gate locally immediately before publication and run the same gate
in CI from a complete checkout. Configure CI without access to private data or
marker files; private comparisons run locally. Newly introduced CI/config files
need their own explicit inventory review. Fail closed on every nonzero exit;
do not publish a redacted match log or upload failed artifacts for diagnosis.
On a history finding, keep publication blocked and have the checkout owner
resolve the affected refs without altering the original private files.

The tests create only temporary synthetic repositories/workspaces. They verify
normal `git add` containment for private roots and generated dashboard paths,
generic tracked baselines, marker detection, index/history checks, manifest
integrity and non-destructive compatibility. A synthetic resume still resolves
through the existing resolver and its original bytes and neighboring private
records remain unchanged. Workspace selection and migration behavior belong to
the separate workspace implementation; this checker never moves or edits data.
