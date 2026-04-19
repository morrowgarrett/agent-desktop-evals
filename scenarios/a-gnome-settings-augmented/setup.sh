#!/usr/bin/env bash
# setup.sh — snapshot the current background color so check_state.sh has a baseline.
set -euo pipefail
gsettings get org.gnome.desktop.background primary-color > /tmp/scenario-a-baseline
echo "snapshotted baseline: $(cat /tmp/scenario-a-baseline)"
