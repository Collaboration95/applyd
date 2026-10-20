# Synthetic dashboard demo

Generate an isolated demo dashboard with 15 fictional applications:

```sh
python3 scripts/demo.py --destination /tmp/applyd-demo
```

The destination contains copies of the public dashboard assets, `dashboard/data.js`, and `dashboard/data/applications.json`. The committed `demo/applications.json` is the only data input. A nonempty destination is refused without changing its files. Only the generated display timestamp changes between generator runs. Application times, fictional records, and synthetic resume filenames are stable. The generator does not read a workspace or write dashboard data into the repository.

Regenerate the browser captures with one command (the default synthetic clock is fixed for repeatable pixels; override `DEMO_GENERATED_AT` to update it):

```sh
scripts/screenshots.sh
```

The script uses an existing Chrome/Chromium executable and macOS `sips`, writes `docs/img/demo-dashboard.png` and a three-frame `docs/img/demo-run.gif`, and gives the browser a new temporary user-data directory for every run. Python's standard library merges the GIF frames. It opens only the generated local `file://` page. It does not install software, reuse a browser profile, or visit job sites. To select a browser binary, set `DEMO_BROWSER=/path/to/chrome`.

If a prerequisite is missing, the command exits with a typed `DEMO_BLOCKER[...]` reason. `HEADLESS_BROWSER_UNAVAILABLE` means an existing Chrome/Chromium executable is needed. `SIPS_UNAVAILABLE` means macOS is required for screenshot cropping and conversion; on Linux, the capture step is unavailable unless an equivalent workflow is added. `SIPS_CONVERSION_FAILED` and `GIF_ASSEMBLY_FAILED` identify capture conversion errors. The browser log stays in the temporary directory and is removed when the command exits; raw logs are not printed on failure.
