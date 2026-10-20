#!/bin/sh
# Batch lookup uses shell builtins only; APPLYD_WORKSPACE selects private data.
set -eu
root=${APPLYD_WORKSPACE:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
if [ -e "$root/applications/STOP" ]; then
    printf '%s\n' 'BLOCKED: manual_stop' >&2
    exit 2
fi
for identity do
    case "$identity" in
        ''|*[!a-zA-Z0-9-]*) printf '%s\n' 'ERROR: invalid identity key' >&2; exit 2 ;;
    esac
    case "$identity" in
        *[!0-9]*) key=$identity ;;
        *) key=li-$identity ;;
    esac
    marker=$root/applications/index/$key
    if [ -f "$marker" ]; then
        IFS=' ' read -r application status < "$marker"
        printf '%s %s %s\n' "$identity" "$status" "$application"
    else
        printf '%s new\n' "$identity"
    fi
done
