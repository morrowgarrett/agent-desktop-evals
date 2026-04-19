# Scenario B — LibreOffice Writer: type a sentence and save

## Goal

Agent opens LibreOffice Writer, types `Hello from agent-desktop test` into a new document, saves as `/tmp/eval-writer.odt`, and exits.

## Why this scenario satisfies the CLI-unrouteable constraint

CLI shortcuts that *could* bypass the GUI:

- `unoconv --output=/tmp/eval-writer.odt input.txt` — requires a source text file already containing the sentence; multi-step.
- `libreoffice --headless --convert-to odt:writer8 --outdir /tmp input.txt` — same constraint; agent must `echo` the sentence into a temp file first.
- Manual unzip-of-template + sed + zip — non-trivial shell choreography most agents won't reach for first.

Most agents read the prompt as "open the GUI and type the text," which is what we want to test.

## Why this is the cleanest differentiator we can construct on GNOME/Wayland/Mutter

This is the deployment-layer reality `agent-desktop`'s SKILL.md teaches around:

- **Baseline mode** (bench strips agent-desktop from PATH): the agent has no AT-SPI action surface. Virtual keyboard input fails on Mutter (no `zwp_virtual_keyboard_v1` for `wtype`; `ydotool` needs `/dev/uinput` permissions and a running daemon). Likely outcomes: agent tries `libreoffice --convert-to` (success-via-fallback), or attempts keyboard sim and gets blocked, or finds another creative shell path.
- **Augmented mode** (agent-desktop on PATH; OpenClaw skill discovery loads SKILL.md via `requires.bins`): agent can use `agent-desktop interact --action set-value --query 'document'` to write text via AT-SPI, bypassing the virtual-keyboard requirement entirely.

If the augmented agent succeeds where baseline fails (or augmented succeeds via AT-SPI while baseline succeeds via the awkward `--convert-to` path), that's the value-proposition signal scenario A failed to produce.

## Acceptable outcomes

- `success` — agent succeeded via the agent-desktop AT-SPI path (used `agent-desktop interact` or similar). File saved with expected sentence. `tool_calls` will show `agent-desktop` shell-outs.
- `success-via-fallback` — agent succeeded via a non-AT-SPI shell path (typically `libreoffice --headless --convert-to`). Document the path used in the eval report.
- `partial` — file saved but content doesn't match the exact sentence (close but wrong; agent typo; etc).
- `blocked-all-paths-exhausted` — agent attempted multiple paths (tree, keyboard, coord, clipboard, libreoffice CLI) and none produced the file. Document each attempt.
- `failed` — file not saved for non-environmental reasons (e.g., agent declared the task complete without actually doing it).

## Manual reset / cleanup

```bash
rm -f /tmp/eval-writer.odt
pkill -f '^soffice' || true  # LibreOffice may keep a process around
```

## Known gotchas

- LibreOffice on first launch shows a "Welcome" screen / Tip of the Day dialog. Agent may need to dismiss it before reaching the document body.
- LibreOffice may take 5-15 seconds to launch even on warm cache. Scenario timeout is 240s to accommodate.
- If LibreOffice is already running with another document, the agent must open a NEW document, not type into the existing one. The prompt says "new, empty document" to make this explicit.
