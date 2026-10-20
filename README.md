<p align="center"><img src="docs/brand/banner.png" alt="applyd — local-first job application agent" width="100%"></p>

**applyd** is a local-first job application agent. It does the repetitive parts of a job search: it checks whether a listing was already handled, captures the listing, fills applications from a saved answer bank, and logs every confirmed submission. You spend your time on interviews and real work instead.

> Status: early. The bookkeeping core (capture, deduplication, answer bank, resume releases, preflight, run-log dashboard) works today. The faster, more deterministic pipeline and the control-plane dashboard are tracked in the [roadmap](https://github.com/users/Collaboration95/projects/17).

## How it works

```
search results ─▶ seen check ─▶ extract listing ─▶ capture ─▶ prepare answers ─▶ preflight ─▶ submit ─▶ log + cleanup
                 (skip repeats)  (current page only)            (saved answer bank)  (hard gates)        (confirmed only)
```

- **Deterministic first.** Duplicate checks, listing capture, answer selection and pre-submit gates are plain scripts with no model calls. The agent only handles what actually needs judgement, such as unfamiliar form fields.
- **Nothing counts until confirmed.** A job is marked `submitted` only after the confirmation is observed and saved. An ambiguous click becomes `submission_uncertain` and must be reconciled before any retry.
- **Answers have provenance.** Every reused answer records its source, scope and expiry. An unknown material fact is queued for you, never invented.
- **Private by default.** Your profile, resumes, applications and exports stay on your machine. The repository `.gitignore` denies everything except audited generic code.

## Quickstart

Requirements: macOS, Python 3.11+ (standard library only) and Node 18+ for the dashboard tests.

```sh
git clone https://github.com/Collaboration95/applyd.git && cd applyd
python3 -m unittest discover -s tests -v       # verify the core rules
python3 scripts/workspace.py --help             # status, answer, draft, release, capture, event, preflight, export
open dashboard/index.html                       # run log (works from file://, no server)
```

A guided `init` command, a settings file and a synthetic demo workspace are on the roadmap. Until they exist, private workspace folders (`profile/`, `resumes/`, `projects/`, `applications/`) are created locally and never committed.

## Typical flow

```sh
python3 scripts/workspace.py capture --file job.json                     # save the full listing; duplicates are detected
python3 scripts/workspace.py event --id <app> --status preparing
python3 scripts/workspace.py event --id <app> --status ready --resume-scope <scope> --answers answers.json
python3 scripts/workspace.py preflight --id <app>                        # must pass right before Submit
python3 scripts/workspace.py event --id <app> --status submitted --evidence confirmation.txt
python3 scripts/workspace.py export                                      # CSV + dashboard data
```

See [`applications/job-input.example.json`](applications/job-input.example.json) for the input format.

## Layout

| Path | Purpose |
|---|---|
| `scripts/` | `workspace.py` CLI, resume resolution, dashboard export, PDF import |
| `dashboard/` | Static run-log UI (plain HTML/CSS/JS) |
| `tests/` | Behavioural tests with synthetic fixtures |
| `roadmap/` | Backlog source (`backlog-v2.json`) and issue drafts |
| `docs/brand/` | Mascot sprite, sprite sheet, banner and their generator |
| `master-plan.md` | Design invariants and the original backlog |

## Roadmap

- **Step 1: stability.** Ultrafast seen-check, a deterministic listing extractor, staying on the listing page, answer preparation, and dashboard robustness.
- **Step 2: features.** Run settings (Easy Apply only, save-only, dry-run, caps), account-safety stop, tab cleanup, a control-plane dashboard, outcome tracking, draft-only follow-ups, and a public demo.

Board: [applyd — Roadmap](https://github.com/users/Collaboration95/projects/17). Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Responsible use

applyd is designed to keep you in control. It does not scrape profiles or guess email addresses, and it never sends messages on your behalf: outreach is produced as drafts for you to send. Respect the terms of the sites you use and keep runs bounded.

## License

[MIT](LICENSE)
