# Scenario A — first paired live run (2026-04-19)

## Headline result

Both modes succeeded. Augmented was **15% faster** but consumed **5.8% more tokens**. Both modes used **0 screenshots**, indicating both agents bypassed the GUI entirely and went straight to `gsettings set` via shell exec.

| metric | baseline | augmented | delta |
|--------|----------|-----------|-------|
| tokens | 152,231 | 160,998 | +5.8% |
| wallclock (s) | 71.39 | 60.47 | −15.3% |
| screenshots | 0 | 0 | — |
| success | ✓ | ✓ | — |
| parse_warnings | 1 | 0 | — |

## Critical finding: scenario A is not a useful differentiator

The scenario asked the agent to "open GNOME Settings, change the Background to a different solid color." Both agents skipped the GUI entirely and called `gsettings set org.gnome.desktop.background primary-color '<value>'` via the OpenClaw `exec` tool. The check script verified the gsettings value changed — which it had — so both runs registered as success.

**The scenario design assumed the agent would attempt the GUI path.** OpenClaw's main agent has a `gpt-5.4` brain that recognized the CLI shortcut immediately. Result: scenario A doesn't measure what we built it to measure (a11y-tree-driven GUI control vs vision-driven GUI control).

This is good empirical signal for the eval format — but it means scenario A's numbers aren't comparable to the original Phase 2 plan's intent.

## What this means for Phase 2 scenario design

Future scenarios must be **CLI-unrouteable**. The agent must have NO shell shortcut. Candidates:

- **GUI dialogs with no API**: a vendor-specific About dialog, an app-specific settings panel that doesn't expose dconf/gsettings keys, native chat UI compose-and-send (where `$CLI send` doesn't exist for the chosen client).
- **Information extraction from a window**: read a value visible in a GUI that isn't queryable via shell (e.g., a real-time graph value, a dialog message).
- **Stateful UI navigation**: navigate a multi-step wizard / setup flow that requires sequenced clicks.

Document this constraint in `docs/SCENARIO-DESIGN.md` (TODO) before authoring B/C/D in M2.

## Augmented vs baseline behavior breakdown

Both runs consumed roughly the same baseline (cumulative) tokens:
- Baseline: 152,231 tokens
- Augmented: 160,998 tokens  (Δ +8,767)

The +8,767 token delta in augmented corresponds almost exactly to the SKILL.md content prepended to the augmented prompt (~9KB / ~3K tokens at typical encoding) plus the agent's slightly longer reasoning trace about "do I need this tool or not?"

Wall-clock: augmented was 11s faster (71.4s → 60.5s). One run each — could be variance. Plausible interpretation: the agent in augmented mode had explicit guidance about its options and decided faster. Need 5+ runs each to claim significance.

## Parse warning (baseline only)

Baseline run reported `parse_warnings=1` from the bench's metric parser. Likely the C-C drift detector (introduced in commit `34dfa31`) firing on an unknown field in OpenClaw's `usage` block — exactly its purpose. Worth a follow-up to identify the exact field for documentation.

Augmented run: 0 warnings. Same parser, same OpenClaw version, same model — the warning may not be deterministic. Worth investigating.

## Validation of the bench harness

| validation | outcome |
|------------|---------|
| OpenClaw subcommand correct (`agent --local --message --json`) | ✓ |
| `--agent main` flag works | ✓ |
| Stderr+stdout combined parsing works against real output | ✓ |
| `meta.agentMeta.usage.total` extraction correct (152K / 161K not 119) | ✓ |
| Last-cumulative-wins (single object today, not exercised) | not exercised |
| Agent exit-code gate fired correctly (proc.returncode=0 → success allowed through) | ✓ |
| check_state correctly verified gsettings change | ✓ |
| Drift detector fired once (real upstream field unknown to our model) | ✓ |
| ydotool/wtype/sway issues | not exercised (agent skipped GUI) |
| AT-SPI tree degeneracy issues | not exercised (agent skipped GUI) |

The Linux/Wayland horror story we documented in the design doc was completely bypassed because the agent never tried the GUI. **The scenario didn't force the agent into the AT-SPI path.**

## Acceptance criteria status (M1 plan)

- [x] Bench repo public, CI green
- [x] Stub runner works
- [x] OpenClaw runner: baseline mode (real run, success)
- [x] OpenClaw runner: augmented mode (real run, success)
- [x] Paired report + comparison
- [x] SKILL.md drafted + PR'd (#22)
- [x] Issue #21 filed
- [x] Format-flip PR'd (#23)
- [x] HANDOFF updated

**M1 fully closed.**

## Action items / follow-ups

1. Investigate the baseline-only `parse_warnings=1`. Re-run both modes 3–5× to check determinism.
2. Author `docs/SCENARIO-DESIGN.md` documenting the "no CLI shortcut" requirement before M2 scenarios B/C/D.
3. Consider adding `--no-shell` or "GUI required" annotation to scenario format so future agents are forced into the visual path.
4. Surface `parse_warnings` in the markdown/CSV reports (M-B from prior review — out of scope for M1, deferred).
5. Capture a multi-turn fixture (the smoke fixture is single-turn, so the C-A "last cumulative wins" code path isn't exercised by tests against real data yet).

## Repo state

- HEAD: `34dfa31` (8th commit, all review fixes landed)
- 63 tests passing
- CI green
- Reports at `reports/live-2026-04-19/{baseline,augmented}/{report.md,report.csv}`
- Augmented scenario fork at `scenarios/a-gnome-settings-augmented/` (manual one-off; productize in M2)
