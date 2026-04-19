# Scenario design

This document captures the constraints and patterns that make eval scenarios produce useful baseline-vs-augmented signal. It is derived from empirical lessons of scenario A (`a-gnome-settings`), which had a CLI shortcut (`gsettings`) that defeated the value proposition the scenario was meant to test.

The bench measures what an agent does, not what we wish it would do. If a scenario has a way to succeed without the GUI, capable agents will find that way and the augmented vs baseline numbers will reflect "shell vs shell with extra context" — not "GUI control vs no GUI control."

## The core rule

**Scenarios must be CLI-unrouteable.** A capable agent given shell access (which OpenClaw's default `main` agent has via `exec`) must have no path to satisfy `check_state.sh` other than driving the GUI.

## Anti-patterns (do not use these as scenario targets)

- **Anything queryable or settable via `gsettings` / `dconf` / `setxkbmap` / similar config CLIs.** Wallpaper color, keyboard layout, default app handlers, accessibility toggles — all of these have CLI mutators. Agent will skip the GUI.
- **Anything backed by a structured config file the agent can edit directly.** Theme JSONs, font configs, MIME associations, autostart `.desktop` files. If a `cat`/`sed` works, the agent will use it.
- **App settings exposed via app-specific CLI.** `firefox --new-tab`, `gnome-terminal --command`, `code --install-extension`. Read each candidate app's `--help` before authoring.
- **State queryable via `dbus-send` / `gdbus call`.** Many GNOME apps publish their state on the session bus (window listings, notification state, media player position). The agent has shell + dbus knowledge.
- **State observable via `wmctrl` / `xdotool getactivewindow` / `swaymsg -t get_tree` / equivalents.** Window titles, focus state, geometry — all CLI-accessible.
- **Filesystem state.** "Create a file" / "rename a file" — `touch` and `mv` are too obvious.

## Patterns that are CLI-unrouteable

- **Read a value displayed only in a GUI** that has no shell exposure. Examples to verify candidacy by `$ <CLI> --help` survey:
  - The progress percentage displayed in a GUI installer or download dialog.
  - The currently-selected tool/state in a creative app (Inkscape current zoom level, GIMP foreground color, LibreOffice cursor position).
  - The text content of an arbitrary native dialog (e.g., a software EULA, an "About" dialog whose content isn't in `/etc/*-release`, a one-time setup wizard).
  - The list of items currently displayed in a chat client's main pane (Telegram Desktop conversation titles, Element room list).
- **Manipulate a stateful UI element with no programmatic shortcut.** Examples:
  - Drag-and-drop a file from one Files pane to another (within the same app — across separate apps, just use `mv`).
  - Reorder items in an app's UI (toolbar customization, playlist reordering).
  - Operate a multi-step wizard whose intermediate state isn't persisted between steps.
- **Interact with content rendered inside a Canvas/Electron-with-broken-a11y/games surface** — these are the cases vision-first wins anyway, but they're also the cases where a11y MAY work, MAY not, and the data tells the story.

## Verification (`check_state.sh`) design

`check_state.sh` is the oracle. It must:

1. **Verify success in a way that doesn't reveal a CLI shortcut to the agent.** If the check itself uses `gsettings get`, the agent's intelligence will route through that. If it must use such a query, ensure the corresponding `set` is structurally harder than the GUI path (rare in practice).
2. **Be deterministic.** Don't depend on time, network, system load.
3. **Be reversible by `setup.sh`.** Pair every state-mutating scenario with a setup script that snapshots and a manual reset path.
4. **Not require the bench to know the agent's chosen value.** For "change the background," the check is "did the value change from baseline" not "is it `#5a2a7a`."

Example for an Inkscape zoom-level scenario:

```bash
#!/usr/bin/env bash
# check_state.sh — verify Inkscape document zoom changed from baseline
set -euo pipefail
# Read the .svg file the agent saved (the zoom level lives in metadata)
test -f /tmp/eval-inkscape-output.svg || exit 1
NEW_ZOOM=$(xmllint --xpath 'string(//*[local-name()="namedview"]/@*[local-name()="zoom"])' /tmp/eval-inkscape-output.svg 2>/dev/null)
BASELINE_ZOOM=$(cat /tmp/scenario-b-baseline-zoom)
[ "$NEW_ZOOM" != "$BASELINE_ZOOM" ]
```

## Tool-call expectations (the bench's `tool_calls` field)

A well-designed scenario should produce these patterns:

- **Baseline mode**: agent attempts shell-only paths, fails or partially succeeds. Expected `tool_calls`: high `exec` count, possibly `web_search` or `read` (for docs), no `agent-desktop` shell-outs.
- **Augmented mode**: agent uses agent-desktop. Expected: `read` (loading SKILL.md), then `exec` calls whose argv starts with `agent-desktop` (observe, click, type, etc.). The `tool_calls.exec` count alone doesn't tell us; we'd need to break down by argv prefix to see "agent-desktop calls vs other shell calls."

Until the bench can break down `exec` by argv prefix (M1.5 follow-up), inspect transcripts manually for `agent-desktop` mentions.

## Outcome taxonomy reminder

(From `evals/EVAL_FORMAT.md` in the upstream agent-desktop fork.)

A scenario's allowed outcomes:

- `success` — task completed via the canonical (tree/agent-desktop) path.
- `success-via-fallback` — task completed via a non-tree path. Document which.
- `partial` — task partially completed; agent reported what it could.
- `blocked-tree-inaccessible` — `observe` returned a degenerate tree; agent did NOT exhaust non-tree paths. Use only if the scenario explicitly limits to tree path.
- `blocked-all-paths-exhausted` — agent tried tree, key, click-coord, clipboard; none worked. Document each attempt.
- `failed` — agent had a viable path but result didn't match expected outcome.

A CLI-unrouteable scenario should remove `success-via-fallback` from the acceptable set (since "fall back to shell" is what we're trying to prevent).

## Authoring checklist

Before committing a new scenario:

- [ ] Identified target app + target state change.
- [ ] Verified no CLI shortcut: ran `man <app>`, `<app> --help`, `gsettings list-schemas | grep <app>`, `gdbus introspect --session --dest org.<app>.* --object-path /org/<app>` (where applicable). Documented in scenario README why each candidate shortcut doesn't apply.
- [ ] `setup.sh` snapshots baseline state to `/tmp/scenario-<id>-baseline*`.
- [ ] `check_state.sh` exits 0 on success, ≠0 on failure. Doesn't depend on time / network / system load.
- [ ] `prompt.md` describes the goal in user terms, NOT in tool terms. Don't say "use `agent-desktop click`" — say "open the X panel and toggle Y."
- [ ] Manual reset path documented in README so test runs are repeatable.
- [ ] Acceptable outcomes specified in the scenario README, with `success-via-fallback` explicitly disallowed for CLI-unrouteable scenarios.
- [ ] Ran the scenario manually to verify the GUI path works at all (a scenario you can't complete by hand isn't a valid test).
- [ ] Ran the scenario via the bench in BOTH modes and inspected `tool_calls` to confirm the agent's behavior matches the scenario's intent.

## On the "augmented prompt" question

Currently the bench's augmented mode uses a forked scenario directory with the SKILL.md content prepended to `prompt.md`. Empirically, the inlined SKILL.md is **redundant** when the agent's local `~/skills/agent-desktop/SKILL.md` exists with `requires.bins: ["agent-desktop"]` — OpenClaw's skill discovery handles the toggle via the bench's PATH-strip in baseline mode.

**Recommendation**: stop forking the scenario for augmented mode. Run baseline AND augmented against the same scenario directory; let the bench's PATH manipulation (which already differentiates the modes) be the only difference between runs. This eliminates the duplicate-files drift risk noted in `findings.md`.

This becomes the architecture for M1.5 #3 (scenario refactor).
