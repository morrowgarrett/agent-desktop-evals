#!/usr/bin/env bash
# setup.sh — clean prior eval output so the run starts from a known state.
set -euo pipefail
rm -f /tmp/eval-writer.odt
echo "cleaned /tmp/eval-writer.odt"
