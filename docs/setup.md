# Setup

Use Python 3.11 or newer. Node.js 18 or newer is required for dashboard tests.
The setup and diagnosis helpers use Python's standard library; they install no
packages and do not contact a network service.

Initialize an empty private workspace:

```sh
python3 scripts/workspace.py --workspace /path/to/private-workspace init
```

Then inspect local prerequisites:

```sh
python3 scripts/workspace.py --workspace /path/to/private-workspace doctor
```

`init` accepts a path that does not exist or an existing empty directory. It
creates empty settings, answers, project, resume registry and preset JSON, the
application directories, and copies the generic static dashboard files so the
dashboard opens directly with `file://`. It refuses any root that already has
entries. Run it only once per new workspace; it never replaces existing data.

`doctor` is read-only. It checks the Python and Node versions, and on macOS it
checks for an installed browser application; automation and login remain unverified. It does not launch a
browser or inspect, open, or change an authenticated browser session. On other
operating systems, browser capability is reported as unsupported. PDF tools
(`pdftotext`, `pdfinfo`, or PyMuPDF) are optional. Reports contain generic check
names and statuses, not executable or workspace paths.

Verify the setup behavior with synthetic temporary workspaces:

```sh
python3 -m unittest tests.test_setup_workspace -v
```
