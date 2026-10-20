# Setup

## Requirements

- Python 3.11 or newer.
- macOS or Linux with POSIX `fcntl` support. Native Windows is unsupported because workspace locking uses `fcntl`; use WSL.
- No Python packages, network service, database, framework, or local server is required for setup or the synthetic demo. The dashboard is plain static HTML, CSS, and JavaScript and opens from `file://`.
- Node.js 18 or newer is needed only for the optional dashboard JavaScript tests, not for setup or the demo.

## Initialize a private workspace

Choose a new path outside the checkout. `init` accepts a path that does not exist or an existing empty directory. It refuses a nonempty directory before writing and does not overwrite existing data.

```sh
export APPLYD_WORKSPACE="$HOME/.local/share/applyd-workspace"
python3 scripts/workspace.py --workspace "$APPLYD_WORKSPACE" init
```

Initialization creates empty settings, answer bank, project catalog, resume registry and preset JSON files, application directories, and copies the generic dashboard assets. It does not populate personal facts or resumes. Keep the private workspace outside the public code checkout.

Edit the generated settings before using the agent. For an initial practice run, set `mode` to `"dry_run"`:

```sh
"${EDITOR:-vi}" "$APPLYD_WORKSPACE/settings.json"
```

The settings schema and defaults are described in [settings](settings.md). The generated default mode is `apply`; explicitly choose `dry_run` before starting an agent workflow unless you have separately configured and authorized a live run. Workspace settings do not provide a resume, answer bank, browser session, or user authorization.

## Generate the synthetic demo

The demo reads only the checked-in fictional fixture and writes a standalone dashboard to the caller-selected destination. Keep this destination separate from the private workspace. A nonempty destination is refused without changing its contents.

```sh
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/applyd-demo.XXXXXX")"
python3 scripts/demo.py --destination "$DEMO_ROOT"
open "$DEMO_ROOT/dashboard/index.html"       # macOS
# xdg-open "$DEMO_ROOT/dashboard/index.html"  # Linux desktop
```

This does not read workspace records, open a browser automation session, visit a job site, or require private resume or browser data. The dashboard can also be opened directly in a browser from its generated `file://` URL.

## Diagnosis and optional screenshot capture

`doctor` reports local prerequisites without launching or inspecting an authenticated browser. Python is required. Node.js is reported because it is used for dashboard tests, and PDF extraction tools are optional.

```sh
python3 scripts/workspace.py --workspace "$APPLYD_WORKSPACE" doctor
```

Regenerating the repository's screenshot assets is a separate maintainer task, not part of setup or demo generation. `scripts/screenshots.sh` requires an already installed Chrome/Chromium executable and macOS `sips`; it opens only the generated local demo page. It does not install software or reuse a browser profile. Screenshot capture is optional and is not required to view the committed `docs/img/demo-dashboard.png` and `docs/img/demo-run.gif` or to generate the demo.

The behavior of workspace initialization is covered by the focused synthetic test:

```sh
python3 -m unittest tests.test_setup_workspace -v
```
