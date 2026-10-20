#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/applyd-demo.XXXXXX")
trap 'rm -rf "$TEMP_ROOT"' EXIT HUP INT TERM

python3 "$ROOT/scripts/demo.py" --destination "$TEMP_ROOT/site" \
  --generated-at "${DEMO_GENERATED_AT:-2026-10-20T12:00:00+08:00}" >/dev/null
PAGE="$TEMP_ROOT/site/dashboard/index.html"

if [ -n "${DEMO_BROWSER:-}" ]; then
  BROWSER=$DEMO_BROWSER
elif [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER=$(command -v chromium)
elif command -v chromium-browser >/dev/null 2>&1; then
  BROWSER=$(command -v chromium-browser)
elif command -v google-chrome >/dev/null 2>&1; then
  BROWSER=$(command -v google-chrome)
elif command -v google-chrome-stable >/dev/null 2>&1; then
  BROWSER=$(command -v google-chrome-stable)
else
  printf '%s\n' 'DEMO_BLOCKER[HEADLESS_BROWSER_UNAVAILABLE]: Select an existing Chrome or Chromium executable; no browser was installed.' >&2
  exit 2
fi

# Check the real rendered DOM: a zero browser exit does not prove scripts booted.
if ! "$BROWSER" --headless --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir="$TEMP_ROOT/browser-profile" --virtual-time-budget=1000 --dump-dom "file://$PAGE" \
  >"$TEMP_ROOT/rendered.html" 2>"$TEMP_ROOT/browser.log"; then
  printf '%s\n' 'DEMO_BLOCKER[CLIENT_RENDER_FAILED]: Could not inspect the synthetic page.' >&2
  exit 2
fi
if ! python3 - "$TEMP_ROOT/rendered.html" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
rows = re.search(r'<tbody\b[^>]*id="application-rows"[^>]*>(.*?)</tbody>', text, re.S)
stats = re.search(r'<section\b[^>]*id="stats"[^>]*>(.*?)</section>', text, re.S)
assert rows and len(re.findall(r'<tr\b', rows[1])) == 15
assert stats and len(re.findall(r'class="stat"', stats[1])) == 7
PY
then
  printf '%s\n' 'DEMO_BLOCKER[CLIENT_RENDER_FAILED]: Synthetic applications or counts did not render.' >&2
  exit 2
fi

if [ ! -x "$BROWSER" ]; then
  printf '%s\n' 'DEMO_BLOCKER[HEADLESS_BROWSER_UNAVAILABLE]: DEMO_BROWSER must name an executable Chrome or Chromium binary.' >&2
  exit 2
fi
if ! command -v sips >/dev/null 2>&1; then
  printf '%s\n' 'DEMO_BLOCKER[SIPS_UNAVAILABLE]: Screenshot cropping and GIF frame conversion require macOS sips.' >&2
  exit 2
fi

mkdir -p "$ROOT/docs/img"
SCREENSHOT="$ROOT/docs/img/demo-dashboard.png"
if ! "$BROWSER" --headless --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir="$TEMP_ROOT/browser-profile" --window-size=1440,2600 \
  --hide-scrollbars --screenshot="$SCREENSHOT" "file://$PAGE" \
  >"$TEMP_ROOT/browser.log" 2>&1; then
  printf '%s\n' 'DEMO_BLOCKER[HEADLESS_RENDER_FAILED]: The existing browser could not render the synthetic local dashboard.' >&2
  exit 2
fi

RUN_SCREENSHOT="$TEMP_ROOT/run.png"
if ! "$BROWSER" --headless --disable-gpu --no-first-run --no-default-browser-check \
  --user-data-dir="$TEMP_ROOT/browser-profile" --window-size=1440,2600 --virtual-time-budget=1000 \
  --hide-scrollbars --screenshot="$RUN_SCREENSHOT" "file://$TEMP_ROOT/site/dashboard/runs.html" \
  >"$TEMP_ROOT/browser.log" 2>&1; then
  printf '%s\n' 'DEMO_BLOCKER[HEADLESS_RENDER_FAILED]: Could not render the synthetic run view.' >&2
  exit 2
fi

HEIGHT=$(python3 - "$RUN_SCREENSHOT" <<'PY'
import struct
import sys

with open(sys.argv[1], "rb") as image:
    header = image.read(24)
if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
    raise SystemExit(1)
print(struct.unpack(">II", header[16:24])[1])
PY
) || {
  printf '%s\n' 'DEMO_BLOCKER[SCREENSHOT_INVALID]: The browser did not produce a readable PNG screenshot.' >&2
  exit 2
}
WIDTH=$(python3 - "$RUN_SCREENSHOT" <<'PY'
import struct
import sys

with open(sys.argv[1], "rb") as image:
    image.seek(16)
    dimensions = image.read(8)
if len(dimensions) != 8:
    raise SystemExit(1)
print(struct.unpack(">II", dimensions)[0])
PY
) || {
  printf '%s\n' 'DEMO_BLOCKER[SCREENSHOT_INVALID]: The browser did not produce a readable PNG screenshot.' >&2
  exit 2
}

FRAME_HEIGHT=720
if [ "$HEIGHT" -lt "$FRAME_HEIGHT" ]; then FRAME_HEIGHT=$HEIGHT; fi
LAST=$((HEIGHT - FRAME_HEIGHT))
MID=$((LAST / 2))
if [ "$LAST" -gt 0 ]; then LAST=$((LAST - 1)); fi
for index in 0 1 2; do
  case $index in
    0) offset=0 ;;
    1) offset=$MID ;;
    2) offset=$LAST ;;
  esac
  if ! sips --cropToHeightWidth "$FRAME_HEIGHT" "$WIDTH" --cropOffset "$offset" 0 \
    "$RUN_SCREENSHOT" --out "$TEMP_ROOT/frame-$index.png" >/dev/null 2>&1 ||
    ! sips -s format gif --optimizeColorForSharing "$TEMP_ROOT/frame-$index.png" \
      --out "$TEMP_ROOT/frame-$index.gif" >/dev/null 2>&1; then
    printf '%s\n' 'DEMO_BLOCKER[SIPS_CONVERSION_FAILED]: macOS sips could not crop or convert a screenshot frame.' >&2
    exit 2
  fi
done

if ! python3 "$ROOT/scripts/demo_gif.py" --delay 90 --output "$ROOT/docs/img/demo-run.gif" \
  "$TEMP_ROOT/frame-0.gif" "$TEMP_ROOT/frame-1.gif" "$TEMP_ROOT/frame-2.gif" >/dev/null 2>&1; then
  printf '%s\n' 'DEMO_BLOCKER[GIF_ASSEMBLY_FAILED]: The standard-library GIF frame merger could not assemble the animation.' >&2
  exit 2
fi
printf '%s\n' 'Wrote docs/img/demo-dashboard.png and docs/img/demo-run.gif (synthetic only).'
