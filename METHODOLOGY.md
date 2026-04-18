# Methodology

## What this is

Evals for [`agent-desktop`](https://github.com/crowecawcaw/agent-desktop) — Stephen Crowe's accessibility-first Rust CLI for AI agents to control desktop UIs. The maintainer publicly noted that the project lacks evals and that having them would let changes ship faster and with more confidence that capability isn't regressing. This repo is one answer to that need: a paired-baseline harness that scores agents on real Linux desktop tasks **with** vs **without** `agent-desktop` available.

The headline output is a per-scenario report comparing the two modes across success, wall-clock seconds, tokens consumed, and screenshots sent. The format is reproducible — clone the repo, install the dependencies, run `bench run`, get the same numbers (modulo the agent's own non-determinism).

## Why this matters beyond agent-desktop

Frontier vision-only agents (Holo3 78.85% on OSWorld-Verified, UI-TARS, Microsoft Fara-7B, Anthropic Computer Use) are dominating capability leaderboards. They are **not** the gap on Linux. The gap is the **deployment layer**:

- Claude Cowork has no Linux client 15+ months after launch.
- OpenAI Operator was cloud-VM-only and was deprecated 2025-08-31.
- Microsoft Recall is Windows-only.
- Apple App Intents is Apple-only.
- The only AT-SPI2 MCP server published as of April 2026 is [`kwin-mcp`](https://github.com/isac322/kwin-mcp) — KDE Plasma 6 Wayland only, explicitly excluding GNOME, Sway, and X11.

These evals score on a deployment-layer metric set (tokens, screenshots, wall-clock, headless capability) where accessibility-first wins, not on raw success rate where vision-first wins narrowly.

## Methodology

We borrow OSWorld's execution-based eval pattern: each scenario has a machine-readable spec + a `check_state.sh` that returns 0 when the desired end state is achieved. We do **not** run inside OSWorld's VM harness. We run on real Linux desktops across display servers, on apps OSWorld's single-VM Ubuntu image doesn't exercise.

Each scenario runs in two modes against each runner:

- **baseline**: `agent-desktop` is removed from `$PATH`. The agent uses whatever else it has (typically screenshot + vision via Anthropic Computer Use, or shell tools).
- **augmented**: `agent-desktop` is on `$PATH` and the SKILL.md is loaded. The agent may use either path; we measure which it picks and what it costs.

## What we measure

| metric | why |
|--------|-----|
| success | binary pass/fail per `check_state.sh` |
| wall-clock seconds | latency matters for interactive use |
| **tokens consumed** | deployment cost; the headline a11y-first metric |
| **screenshots sent** | bandwidth + privacy proxy |
| steps taken | observability |

Token target reference: **<400 tokens per app-window snapshot** via a11y vs 3-5K via screenshots — this is xa11y project-team's stated target.

## What we do not measure

- OSWorld-Verified score. Different benchmark, different fight.
- Pixel-grounding accuracy. Vision benchmarks (ScreenSpot-Pro, etc.) own that.

## Position relative to peers

- **OSWorld** — parent methodology; we cite, we don't compete.
- **Microsoft UFO2 / UFO3** — published peer for hybrid a11y+vision; their architecture validates ours.
- **Playwright MCP** — the snapshot-ref-act browser pattern this is the desktop equivalent of.
- **Vercel agent-browser** — prior CLI snapshot-ref workflow.

## Scope today (M1)

- One scenario: A — GNOME Settings background change.
- One runner: OpenClaw (paired baseline + augmented).
- One display server: GNOME on X11.

## Scope ahead (Phase 2 and beyond)

- More scenarios: native chat (Telegram Desktop / Element), GTK creative (Inkscape / LibreOffice), no-API dialogs (system About).
- More runners: Claude Code, then Cursor, Codex, Gemini.
- More display servers: GNOME on Wayland, KDE Plasma 6.
- More OSes: macOS (agent-desktop already supports it).

The eval harness is designed so each addition is a directory + a few lines of config, not a refactor.
