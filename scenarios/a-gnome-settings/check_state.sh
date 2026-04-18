#!/usr/bin/env bash
# check_state.sh — succeed (exit 0) if the GNOME background primary-color
# differs from the value snapshotted in /tmp/scenario-a-baseline.
set -euo pipefail

BASELINE_FILE="/tmp/scenario-a-baseline"

if [[ ! -f "$BASELINE_FILE" ]]; then
    echo "ERROR: baseline not snapshotted; run setup.sh first" >&2
    exit 2
fi

baseline="$(cat "$BASELINE_FILE")"
current="$(gsettings get org.gnome.desktop.background primary-color)"

if [[ "$baseline" != "$current" ]]; then
    echo "PASS: changed from $baseline to $current"
    exit 0
fi

echo "FAIL: still at baseline $baseline"
exit 1
