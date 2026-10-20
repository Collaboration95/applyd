# Verification and runtime limits

Run from a clean public checkout:

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.test.cjs
python3 scripts/check_publication.py
```

The full Python suite includes `test_release_smoke.py`: capture and exclude listings, queue one shared unknown across two applications, import one confirmed answer once, requeue both jobs, freeze exact files, review a synthetic filled form, interrupt and reconcile submission intent, record fixture confirmations, refuse duplicates and repair an export failure without replay. Original submitted copies remain unchanged. A separate check initializes an empty workspace, runs doctor and generates the 15-record fictional dashboard with all static assets.

The GitHub regression workflow runs those same checks on `ubuntu-24.04`. It uses preinstalled GitHub CLI, Python and Node; it installs no dependencies or services. GitHub access uses `gh repo clone` and `gh pr checkout` with a read-only token limited to the checkout step. Test commands receive no token. No private workspace, browser profile or application data is copied into CI.

Supported runtime is Python 3.11+ on macOS or Linux with POSIX `fcntl`; use WSL for Windows. Node 18+ is needed for JavaScript tests. The dashboard opens directly from disk with no build or server. Screenshot regeneration is optional and requires an existing Chrome/Chromium executable and macOS `sips`.

The repository supplies deterministic helpers and adapter contracts, not an authenticated browser driver. Default `run` performs no browser action when an adapter is absent. Known native form fields and the non-submitting local fixture are tested; unfamiliar or changed layouts stop for inspection. Native file selection requires an audited bridge and actual byte verification. Live employer behavior is not established by fixture tests.

Read-only Gmail sync remains a draft until its required authorized real-inbox check. Live email draft creation also requires a draft-only runtime adapter; without one the outreach CLI returns `credentials_required`. No code sends messages, applies to real roles or installs a scheduler during verification. The scheduler issue remains blocked by its lean-workflow dependency; any future schedule installation is a manual step.

Private data stays outside Git. Explicit generic path allowlists, synthetic canaries, binary metadata checks and full-history scanning protect publication; the roadmap run separately verifies original private file hashes without printing their contents or filenames.
