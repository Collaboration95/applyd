# Contributing to applyd

## Ground rules

1. **No private data, ever.** No real names, contact details, resumes, resume filenames, application records, employer answers, screenshots or generated exports in commits, issues, PRs or logs. Use synthetic fixtures only.
2. **Never force-add.** `.gitignore` denies everything by default. To publish a new generic path, add it to the allowlist in the same PR and explain why it is safe.
3. **Stay small.** Python standard library, JSON records and plain HTML/CSS/JS. No frameworks, databases, hosted services or new dependencies without an issue that justifies them.
4. **Deterministic before model.** If a step can be a script with the same output for the same input, make it a script. Agents handle judgement only.
5. **No live employer actions in tests.** Verification never submits a real application or sends a real message.

## Workflow

- Work from the [roadmap board](https://github.com/users/Collaboration95/projects/17). Pick the highest-priority issue whose `Blocked by` issues are closed.
- One issue per branch (`issue-<number>-<slug>`) and one PR per issue, with `Closes #<number>` in the PR body.
- Meet every acceptance criterion in the issue. Tick the checkboxes in the PR description and say how each was verified.
- Run the full suite before opening a PR:

  ```sh
  python3 -m unittest discover -s tests -v
  node --test tests/*.test.cjs
  ```

- Before pushing, check that only allowlisted generic files are staged: `git status --short` and `git diff --cached --name-only`.

## Roadmap source

`roadmap/backlog-v2.json` is the source for issues. `python3 roadmap/render.py` renders local drafts, and `python3 roadmap/create_github.py` creates any missing issues idempotently. Edit the JSON, not the generated Markdown.

## Regression gate

The GitHub workflow uses an existing Ubuntu runner, preinstalled `gh`, Python and Node. GitHub access goes through authenticated `gh` in a read-only checkout step; the test steps receive no token. It installs no packages or services. The Python suite includes one synthetic release scenario plus fresh init/doctor/demo checks. The JavaScript suite covers the static dashboard, current-page extraction and semantic form adapters. The publication gate scans the index and reachable history.

For the focused release scenario, run `python3 -m unittest discover -s tests -p test_release_smoke.py -v`. It never opens an authenticated browser, submits an application or contacts an employer. Test all browser/provider boundaries with fixtures and stubs.
