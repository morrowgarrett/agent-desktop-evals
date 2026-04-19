# Scenario B — first paired live run (2026-04-19 pass 4)

> **Status: defensible.** Real data with documented methodology limitations. Multiple runs needed for statistical claims, but the qualitative finding is reproducible-in-principle and surprising enough to warrant the writeup.

## Headline finding

**For LibreOffice Writer text input, the SKILL.md saves time by telling the agent NOT to use agent-desktop.** Both modes succeeded via `libreoffice --headless --convert-to`. Augmented mode skipped agent-desktop entirely; baseline tried agent-desktop, hit Wayland-focus errors, and fell back to the same shell path — wasting 3 tool calls and ~30 seconds.

| metric | baseline | augmented | delta |
|--------|----------|-----------|-------|
| tokens | 186,306 | 177,853 | **−4.5%** |
| wallclock (s) | 88.06 | 58.14 | **−34%** |
| tool calls (total) | 7 | 4 | **−43%** |
| agent-desktop calls | 3 (all failed or aborted) | 0 | — |
| outcome | success-via-fallback | success-via-fallback | both via `--convert-to` |
| success | ✓ | ✓ | — |
| File saved correctly | ✓ | ✓ | — |

This is the OPPOSITE of the conventional "augmented should use the special tool" narrative. The SKILL.md's value here is route-the-agent-AWAY from a tool that doesn't help for this task class.

## What each agent actually did

### Baseline (7 exec calls)

1. `libreoffice --writer --nologo --norestore --nolockcheck` — launched GUI
2. `agent-desktop observe --format json` — probed (succeeded, returned all-apps tree)
3. `agent-desktop focus --app soffice` — **FAILED**: "Window focus on Wayland currently requires sway"
4. `agent-desktop observe --app soffice --format json` — re-tried (succeeded, returned soffice tree)
5. `printf 'Hello from agent-desktop test\n' > /tmp/eval-writer.txt && libreoffice --headless --convert-to odt --outdir /tmp /tmp/eval-writer.txt` — fell back to CLI
6. `test -f /tmp/eval-writer.odt && unzip -p ... | grep -F 'Hello...' >/dev/null` — verification
7. `ps -ef | grep '[s]office' | awk '{print $2}' | xargs -r kill` — cleanup

### Augmented (4 exec calls)

1. `libreoffice --writer --nologo --norestore --nolockcheck` — launched GUI
2. `printf 'Hello from agent-desktop test\n' > /tmp/eval-writer.txt && libreoffice --headless --convert-to odt --outdir /tmp /tmp/eval-writer.txt` — went straight to CLI
3. `test -f /tmp/eval-writer.odt && unzip -p /tmp/eval-writer.odt content.xml | grep -F 'Hello...' >/dev/null` — verification
4. `ps -ef | grep '[s]office' | awk '{print $2}' | xargs -r kill` — cleanup

## Methodology gotchas surfaced (must fix before headline writeup)

These don't invalidate this run's qualitative finding, but they undermine quantitative claims about parametric variation across configurations.

### G1: PATH-strip is INEFFECTIVE for OpenClaw's `exec` tool

The bench's `_strip_agent_desktop` correctly removes agent-desktop directories from PATH (verified: `/home/garrett/.cargo/bin` AND `/usr/local/bin` (a symlink) both stripped, and `shutil.which("agent-desktop")` returns None against the stripped PATH).

But OpenClaw's `exec` tool starts a fresh subshell that re-sources the user's shell init files (`~/.bashrc`, `/etc/profile`, etc.), restoring the original PATH. So baseline mode's agent CAN still find and call `agent-desktop` — verified in this run (events 2, 3, 4).

The strip DOES still affect OpenClaw's skill discovery (which checks `requires.bins` against the bench-stripped PATH at OpenClaw startup, before any subshell). So the SKILL.md isn't loaded in baseline mode. That's the actual A/B differentiator — not "binary present vs absent" but "guidance present vs absent."

**Fix needed**: either (a) accept "SKILL.md presence" as the actual experimental variable and document accordingly, (b) add a more forceful baseline (rename binary, use bind-mount overlay, set deny-list env var), or (c) use a fresh OpenClaw agent per run that has SKILL.md only conditionally installed.

### G2: Session contamination across runs

All today's runs used the same OpenClaw session id `fccffb21-...`. The `main` agent has a persistent session log that accumulates context across invocations. The agent in scenario B baseline likely "remembered" agent-desktop from scenario A's earlier augmented run today (where it observed agent-desktop being usable). That memory may explain why baseline mode tried agent-desktop at all despite no SKILL.md being loaded.

**Fix needed**: pass `--session-id $(uuidgen)` to OpenClaw per-run for clean naive-agent runs. Or use a dedicated benchmark agent with no persistent session.

### G3: Single-run, single-environment data

n=1 each mode. Wallclock variance for OpenClaw API calls runs ±20s per minute of execution. The 30-second delta is plausibly real but not statistically established. Need N≥5 paired before quantitative claims.

GNOME/Wayland/Mutter is also one specific environment. Sway, KDE Plasma, GNOME/X11 may produce different baselines.

## What's good signal (defensible without further runs)

- **The bench correctly captures and reports the difference.** All artifacts persist (transcripts, reports, session logs). Findings are reconstructible from disk.
- **Augmented agent succeeded faster on a CLI-routable task.** This is the inverse of the expected "augmented exercises agent-desktop more" narrative. The SKILL.md works because it includes "use shell tools for file operations" as guidance.
- **Baseline agent's exploration cost** (3 wasted agent-desktop calls + ~30s) is a real cost of NOT having the guidance, even when the binary is reachable.

## What's not signal (yet)

- **Whether agent-desktop's AT-SPI value-prop holds for tasks that genuinely require it.** This scenario ALSO had a CLI shortcut (`--convert-to`). The SCENARIO-DESIGN.md lessons applied, but `--convert-to` is sneakier than `gsettings`. We need a scenario where NO shell path exists at all. Candidates: Slack/Telegram/Discord interaction (no CLI for chat state mutation), Inkscape/GIMP creative state (not installed here), notification dismiss (gdbus shortcut exists but obscure).
- **Whether agent-desktop helps when used.** Baseline tried agent-desktop briefly but gave up (Wayland focus error) before doing real work with it. Augmented didn't even try. So this run produced no data on agent-desktop's actual effectiveness when it IS used end-to-end.

## Concrete next steps (M1.5 backlog refresh)

1. **Fix G1 + G2** before any further paired runs. The methodology gotchas mean current numbers can't be defended in a writeup.
2. **Refactor bench**: pass `--session-id $(uuidgen)` to OpenClaw per run; consider stronger baseline binary-hiding mechanism.
3. **Author scenario C** that's CLI-truly-unrouteable. Telegram/Slack chat state mutation is the strongest candidate on this machine.
4. **Run N≥5 paired** for B with clean methodology before any quantitative claim.
5. **Then** the writeup angle becomes defensible: "SKILL.md guides agent decisions even when the tool is reachable; the value is in the routing not just the access."

## Bench artifacts

- Bench HEAD at run time: `580e51b` (SCENARIO-DESIGN.md commit)
- Reports: `reports/live-2026-04-19-pass4/{b-baseline,b-augmented}/{report.md,report.csv}`
- Transcripts: `reports/raw/b-libreoffice-write/{baseline,augmented}/<timestamp>-<hash>.txt`
- Session log used: `~/.openclaw/agents/main/sessions/fccffb21-508e-4a9c-ac4b-a0b49224361d.jsonl` (private; events extracted to `/tmp/scenario-b-{baseline,augmented}-events.jsonl` for analysis)

## CSV/markdown reports missing tool_calls column (M1.5 follow-up)

The report files don't include tool_calls — that field is on RunResult and surfaces in CLI output but the markdown/CSV renderers weren't updated when M1.5 #2 added it. Easy follow-up commit. Tool-call data in this findings doc was extracted by hand from the session log.
