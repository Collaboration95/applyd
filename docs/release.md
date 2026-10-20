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

Read-only mail import is verified with synthetic messages, durable evidence and repeat-run recovery. A live inbox still requires an authorized read-only adapter and the manual checks in [mail setup](gmail-sync.md); that credential-dependent check was waived for roadmap closure and was not performed. Live email draft creation requires a draft-only runtime adapter; without one the outreach CLI returns `credentials_required`.

The [scheduled-run wrapper](schedule.md) provides bounded local execution, STOP/account-safety checks, registered-tab cleanup, a private summary and an optional local desktop notification. Verification uses synthetic workspaces, clocks and stub adapters. Installing a cron or launchd trigger is a documented manual step. No test sends messages, applies to real roles or installs a recurring schedule.

The maintainer also waived rewriting historical private instructions and batch records. Their original bytes remain intact; new briefs use the generic target/settings template and lean skills.

Private data stays outside Git. Explicit generic path allowlists, synthetic canaries, binary metadata checks and full-history scanning protect publication; the roadmap run separately verifies original private file hashes without printing their contents or filenames.
