# Scenario A — GNOME Settings: change Background → Solid color

## Goal

The agent must open GNOME Settings, navigate to **Background**, switch to a **solid color** (any color other than the current one), and apply.

## Why this scenario

GNOME Settings is the canonical native-app surface for Linux desktop control. It has no first-class CLI for the in-UI navigation flow (gsettings exists for the underlying state, but the UI sequence is what an agent must discover via the a11y tree). It's safe — the change is cosmetic and easily reverted.

## Success criteria

`check_state.sh` reads `gsettings get org.gnome.desktop.background primary-color` and compares it to the baseline written by `setup.sh` (in `/tmp/scenario-a-baseline`). Success = value changed.

## Manual reset

After running, revert with:
```bash
gsettings set org.gnome.desktop.background primary-color "$(cat /tmp/scenario-a-baseline)"
```

## Known caveats

- Requires a GNOME session. On Wayland, agent-desktop's window-focus is sway-only as of v0.1.2, so the agent may not be able to focus the Settings window via `agent-desktop focus`. Workaround: launch via shell first, then observe.
- gnome-control-center 46+ uses a search-driven panel layout; older versions have a sidebar. SKILL.md describes both.
- The `--ref` IDs in agent-desktop's snapshot are stable per process — re-snapshot if Settings restarts.
