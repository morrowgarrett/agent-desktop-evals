# Scenario B — pass-5 paired live run (2026-04-19)

> **Status: this is the first methodologically-clean paired run.** Pass-4 numbers are now known to be heavily distorted by session contamination (G2 from prior findings); pass-5 uses the G2 fix (`--session-id $(uuidgen)` per run, commit `745eab1`).

> **Token-count correction (2026-04-19):** the originally reported pass-5 token counts (22,994 / 21,967) were wrong by ~12×. Root cause: OpenClaw's `meta.agentMeta.usage.total` is overwritten per call (matches `lastCallUsage.total`) rather than accumulated across turns; the bench's `_tokens_from_usage` was preferring `total` over the cumulative `input/output/cacheRead/cacheWrite` sum. Fixed in `fix(runner): always sum cumulative usage components (do not trust usage.total)`. Numbers below have been re-derived from the persisted transcripts in `reports/raw/b-libreoffice-write/{baseline,augmented}/`.

## Headline result

Both modes succeeded via the same `libreoffice --headless --convert-to` shell fallback. Both ATTEMPTED `agent-desktop` substantively (observe + interact/type calls). **Both got blocked by issue #24 (AT-SPI window focus gated behind sway-only on Mutter Wayland)** and fell back to shell.

The clean-session data tells a different story than pass-4:

| metric | baseline pass-5 | augmented pass-5 | delta |
|--------|-----------------|------------------|-------|
| tokens (cumulative billable, corrected) | 273,800 | 266,708 | −2.6% |
| tokens (originally reported, now known wrong) | 22,994 | 21,967 | −4.5% |
| wallclock (s) | 97.79 | 103.80 | +6.1% |
| total tool calls | 21 | 16 | −24% |
| `read` calls (orientation) | 7 | 1 | −86% |
| `agent-desktop` shell-outs | 4 | 4 | 0 |
| `process` calls | 0 | 2 | new |
| `parse_warnings` | 3 | 0 | C-C detector firing on baseline |

The corrected token numbers narrow the augmented-vs-baseline gap (−2.6% vs the formerly reported −4.5%); the difference is well inside single-run variance for OpenClaw API calls. Augmented is 24% lower tool calls overall, mostly because it loaded less orientation context. **It is NOT meaningfully cheaper in tokens, NOT faster, AND ran into the same Wayland blocker as baseline.**

## What both agents actually did (paraphrased traces)

### Baseline (clean session, 21 tool calls)

1-7. Read 7 docs: superpowers SKILL.md (`using-superpowers`), local `~/skills/agent-desktop/SKILL.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, two daily memory files. (Heavy orientation because no prior context.)
8-11. Environment probes: `pgrep at-spi2-registryd`, `gsettings get toolkit-accessibility`, `XDG_SESSION_TYPE`, `command -v libreoffice`.
12-13. Launch LibreOffice + check at-spi2 registry status.
14-17. Try agent-desktop: `observe` (global), `observe --app soffice`, `click --query 'button[name="OK"]'`, `interact --element 11 --action press`.
18. Kill libreoffice (give up on GUI path).
19-21. Fall back: `printf > /tmp/eval-writer.txt && libreoffice --headless --convert-to odt`, then verify, then check matched.

### Augmented (clean session, 16 tool calls)

1. Read 1 doc: local `~/skills/agent-desktop/SKILL.md`. (Less orientation needed because skill IS in OpenClaw's toolkit registry — agent doesn't need to discover it from scratch.)
2-5. Environment probes: at-spi2-registryd, toolkit accessibility, session type, etc.
6. Launch LibreOffice (`libreoffice --writer`).
7. `agent-desktop observe` (global).
8. `process poll` (wait for soffice to start).
9. `agent-desktop observe --app soffice` (now it's running).
10. **`agent-desktop type --app soffice --text "Hello from agent-desktop test"`** — actually attempted the AT-SPI type path.
11. `agent-desktop observe --app soffice --list-roles` (probably checking what's actually exposed after type failed).
12-13. Fall back: `printf > .txt && libreoffice --headless --convert-to`.
14-15. Verify (`test -f` + python zipfile read).
16. `process kill` (cleanup).

## Why both modes ended at the same fallback

Step 10 of augmented (`agent-desktop type --app soffice ...`) is the value-prop test. It would have failed because `--app` triggers `focus_app` which is sway-only on Wayland (per issue #24 we filed). The agent then went to step 11 (list roles), saw the AT-SPI tree was usable but couldn't act on it via the gated focus, and fell back.

**Until issue #24 is resolved, no agent-desktop input-heavy scenario will produce a "augmented succeeds where baseline fails" result on GNOME/Wayland.** The bench is correctly measuring this; the limitation is the upstream tool, not our methodology.

## Pass-4 vs pass-5 comparison (the cost of session contamination)

The pass-4 numbers we previously celebrated were largely artifact. Note that the pass-4 token counts here are themselves under-counted (same `usage.total` bug — see the correction note at the top); they remain comparable to each other within pass-4, but not directly to the corrected pass-5 sums:

| | pass-4 (contaminated, OLD formula) | pass-5 (clean, OLD formula) | pass-5 (clean, CORRECTED) | what changed |
|---|---|---|---|---|
| baseline tokens | 186,306 | 22,994 | 273,800 | pass-4 had accumulated state across runs; pass-5 is one fresh session; correction is the cumulative-components fix |
| augmented tokens | 177,853 | 21,967 | 266,708 | same |
| baseline tool calls | 7 | 21 | 21 | pass-4's agent had prior context; pass-5 has to discover everything |
| augmented tool calls | 4 | 16 | 16 | same |

Two distinct issues collided here:
1. **G2 (session contamination, fixed by commit `745eab1`)**: pass-4 reused the same OpenClaw session across runs, so `usage.input/output/cacheRead` accumulated across days of work — making the metric non-comparable across runs.
2. **F2 (token-formula bug, fixed in this commit)**: `_tokens_from_usage` preferred `usage.total`, which OpenClaw overwrites per call. Multi-turn runs reported the LAST call's billable, not the session's cumulative. Pass-5 (originally) reported 22,994 / 21,967 because every multi-turn run hit this; the true cumulative billable for those same fresh sessions is 273,800 / 266,708.

Pass-5 with the corrected formula is the FIRST run we can defensibly compare across modes (same session length both modes, both fresh, both correctly summed).

## Methodology gotchas remaining

### G1 still holds: PATH-strip is cosmetic

Baseline mode's agent tried `agent-desktop observe` (step 14 of baseline trace) successfully. PATH-strip removed `~/.cargo/bin` and `/usr/local/bin` from the bench's environment, but OpenClaw's `exec` subshell re-sources shell init and finds the binary anyway. So **the actual A/B variable is "skill registered in OpenClaw's toolkit" vs "skill not registered" — NOT "binary present vs absent."**

To get a TRUE binary-absence baseline, we'd need to:
- Rename/move the binary at run start, restore at end
- Use a bind-mount overlay
- Replace the binary with a stub that errors out
- Or run baseline in a container without agent-desktop installed

### G3 still holds: n=1

One paired run. Corrected tokens were within 2.6% of each other; wallclock within 6.1%. Both within plausible single-run variance for OpenClaw API calls. **Need N≥5 paired before any quantitative claim.**

### New: the C-C drift detector fired 3x in baseline, 0x in augmented

Same OpenClaw, same model, same parser. The 3 baseline parse_warnings indicate the C-C drift detector found something unknown in the JSON. We have the transcript persisted (per M1.5 #1) so this is now investigable. Worth a follow-up to identify the unknown field — could be a real schema drift signal or a benign edge case. Not blocking.

## Defensible writeup angle (after this run)

**"On GNOME/Wayland with the OpenClaw `main` agent (gpt-5.4), augmented mode (with the agent-desktop skill in OpenClaw's toolkit) reduced total tool calls by 24% but did not produce successful AT-SPI-driven task completion. Both modes fell back to the same `libreoffice --headless --convert-to` shell path because issue #24 (AT-SPI focus gated behind sway-only window focus) blocks the AT-SPI input path on Mutter. Until upstream fixes #24, agent-desktop cannot demonstrate its input-side value proposition on the most common Linux desktop (GNOME). Read-only AT-SPI paths (`observe`, `read`) work correctly and were exercised by both modes."**

This is honest, specific, and points at the upstream bug as the bottleneck. Stephen will appreciate it because it gives him empirical evidence for prioritizing #24.

## Bench artifacts

- HEAD at run time: `745eab1` (G2 fix)
- Baseline session log: `~/.openclaw/agents/main/sessions/182412c5-143b-4cba-86f0-a95242f97995.jsonl`
- Augmented session log: `~/.openclaw/agents/main/sessions/19aa3dbf-eef6-43c0-80b7-197a8b5dbe46.jsonl`
- Reports: `reports/live-2026-04-19-pass5/{b-baseline,b-augmented}/{report.md,report.csv}`
- Transcripts: `reports/raw/b-libreoffice-write/{baseline,augmented}/<timestamp>-<hash>.txt` (gitignored)

## Next-actions queue

1. **Investigate parse_warnings=3 in baseline** — what fields triggered the C-C drift detector?
2. **Decide on G1 fix** — strong baseline (rename binary) or accept the "skill registered vs not" framing?
3. **Run N≥5 paired** of B with clean methodology before quantitative claims.
4. **Author scenario C** that's TRULY CLI-unrouteable — for example, Telegram chat read (no CLI for chat scrollback). May still hit issue #24 on Wayland though.
5. **Surface tool_calls in CSV/markdown reports** (M1.5 follow-up; data is already on RunResult).
6. **Update findings-scenario-b.md** with the G2-corrected interpretation (currently asserts pass-4's now-known-distorted numbers as headline).
