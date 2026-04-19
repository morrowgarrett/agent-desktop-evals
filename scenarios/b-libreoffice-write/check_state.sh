#!/usr/bin/env bash
# check_state.sh — verify the agent saved /tmp/eval-writer.odt with the expected sentence.
set -euo pipefail

FILE="/tmp/eval-writer.odt"
EXPECTED="Hello from agent-desktop test"

if [[ ! -f "$FILE" ]]; then
    echo "FAIL: $FILE does not exist"
    exit 1
fi

# .odt is a zip; the text content lives in content.xml. Extract and search.
if ! command -v unzip >/dev/null 2>&1; then
    echo "ERROR: unzip not installed; cannot verify"
    exit 2
fi

CONTENT=$(unzip -p "$FILE" content.xml 2>/dev/null) || {
    echo "FAIL: $FILE is not a valid .odt (no content.xml)"
    exit 1
}

if echo "$CONTENT" | grep -qF "$EXPECTED"; then
    echo "PASS: $FILE contains '$EXPECTED'"
    exit 0
fi

echo "FAIL: $FILE exists but content.xml does not contain '$EXPECTED'"
exit 1
