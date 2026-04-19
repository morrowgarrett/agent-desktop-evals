# Scenario A — first paired live run (2026-04-19)

> **Status: this is a single n=1 paired run with documented methodology gaps. NOT writeup-ready.** A second adversarial review surfaced multiple unsupported claims in an earlier draft of this file. This version is the corrected, claims-restricted record. The empirical work to support a public writeup is tracked as M1.5 (see "What's missing for writeup-readiness" below).

## Raw observations (the only things we can defend)

| metric | baseline | augmented |
|--------|----------|-----------|
| `meta.agentMeta.usage.total` (the bench's `tokens` field) | 152,231 | 160,998 |
| wall-clock seconds | 71.39 | 60.47 |
| screenshots | 0 (see methodology gap below) | 0 (see methodology gap below) |
| success per `check_state.sh` | ✓ | ✓ |
| `parse_warnings` | 1 | 0 |
| Background color before | `'#023c88'` | `'#023c88'` |
| Background color after | `'#202124'` | `'#5a2a7a'` |

The two runs used different scenario IDs (`a-gnome-settings` and `a-gnome-settings-augmented`), so the bench's paired-comparison code path didn't fire. The table above was hand-typed from the two single-mode reports. The bench's `render_markdown` paired-delta logic remains **untested in production**.

## Update 2026-04-19 08:30 — concrete tool-call evidence from re-run

After the M1.5 #1 commit added transcript persistence, baseline was re-run (`reports/live-2026-04-19-pass2/baseline/`). Cross-referencing OpenClaw's session log at `~/.openclaw/agents/main/sessions/<sessionId>.jsonl` (which records all tool calls) revealed the baseline agent's actual behavior:

| # | tool | command | result |
|---|------|---------|--------|
| 1 | `exec` | `gnome-control-center background` (background mode) | "Command still running (pid 37062)" — GUI launched |
| 2 | `exec` | `gsettings set` × 5 keys (picture-uri, picture-uri-dark, color-shading-type, primary-color, secondary-color → `#1f6f4a` solid green) | (no output) |
| 3 | `exec` | `gsettings get` × 3 (verification) | `'#1f6f4a' '#1f6f4a' 'solid'` |
| 4 | `exec` | `ps -ef | grep '[g]nome-control-center' | xargs kill` | (no output) |

**Total: 4 `exec` calls, 0 other tools used.** The agent literally opened the GUI (the prompt said "Open GNOME Settings, navigate to Background"), then bypassed all GUI interaction by writing dconf keys directly, then killed the GUI process (interpreting "exit" literally).

This is concrete data — supersedes prior speculation. Re-run pass-2 totals: tokens=161,388 (vs pass-1 152,231 — variance), wallclock=60.54s (vs 71.39s — variance), parse_warnings=0 (vs pass-1 1 — confirming the warning was non-deterministic / transient OpenClaw event).

**Hold on the augmented run** until M1.5 #2 lands so we can capture the same per-tool data in augmented mode for direct comparison.

## Update 2026-04-19 08:35 — augmented re-run after M1.5 #2

Augmented re-run executed (`reports/live-2026-04-19-pass3/augmented/`). Bench reported `tool_calls=exec:5,read:1` automatically (no manual jq required — the M1.5 #2 work pays off immediately). Cross-checking the session log:

| # | tool | what it did |
|---|------|-------------|
| 1 | `read` | `~/skills/agent-desktop/SKILL.md` — discovered the local OpenClaw skill |
| 2 | `exec` | `gnome-control-center background` — opened GUI |
| 3 | `exec` | **`agent-desktop observe --app gnome-control-center --format json`** — actually exercised the AT-SPI path |
| 4 | `exec` | `gsettings set` × 5 keys (mutation via dconf) |
| 5 | `exec` | `gsettings get` × 3 (verification) |
| 6 | `exec` | `ps + xargs kill` (cleanup) |

**The augmented agent ACTUALLY used `agent-desktop observe`.** It read the skill, opened the GUI, observed the AT-SPI tree, then judged gsettings was simpler for the actual mutation and used that instead. This is exactly the design intent of the SKILL.md guidance ("Not for ... file operations — use exec").

### The A/B test is cleaner than I thought

The skill at `~/skills/agent-desktop/SKILL.md` (created today separately from our PR #22 work) declares `requires.bins: ["agent-desktop"]`. OpenClaw's skill discovery checks `requires.bins` against `$PATH`:
- **Baseline**: bench strips agent-desktop dir from PATH → `requires.bins` unsatisfied → skill NOT loaded → agent only knows shell tools → goes straight to gsettings (4 exec calls, no `read`).
- **Augmented**: PATH intact → skill loaded → agent reads SKILL.md → considers agent-desktop, observes the tree, then decides gsettings is simpler (5 exec + 1 read).

So PATH manipulation implicitly toggles the agent-desktop skill availability. That's the design we want for a clean A/B. Our inlined-SKILL.md-in-augmented-prompt approach was redundant — OpenClaw's skill discovery does the work — but it was also harmless (same skill content delivered twice).

### Concrete pass-3 paired comparison

| metric | baseline pass-2 | augmented pass-3 |
|--------|-----------------|------------------|
| tokens | 145,783 | 157,534 |
| wallclock (s) | 60.81 | 76.61 |
| tool_calls | `exec:4` | `exec:5, read:1` |
| success | ✓ | ✓ |
| Background after | (changed; reset) | (changed; reset) |

Augmented took +15.8s and +11.7K tokens. The cost of "go through the SKILL.md, observe the tree, then fall back to gsettings" vs "just use gsettings." For THIS scenario (CLI workaround exists), the AT-SPI observation step was wasted work — the agent didn't act on what it observed.

### What the bench's tool_calls field gives us going forward

Now we can characterize agent behavior in any future scenario:
- "Did augmented mode actually call agent-desktop?" — observable via tool_calls
- "What's the ratio of agent-desktop usage to shell exec?" — `tool_calls.get("exec", 0) vs tool_calls.get("agent-desktop:N")` (where N = subcommand counts; need a follow-up to break down exec calls by argv prefix)
- "Was a SKILL.md actually loaded?" — observable via `read` calls of skill files

This is the empirical foundation the writeup needs. Single-run still applies — we need N≥5 for variance — but the qualitative signal (agent-desktop USED but not RELIED-ON) is real and reproducible.

## What this run does NOT tell us (now narrower)

- **Whether the augmented agent would behave differently.** The augmented run from pass-1 wasn't captured with transcript persistence. Need to re-run augmented after #2 (parser surfaces tool calls from session log) to compare.
- **Whether the augmented prompt actually exercised agent-desktop.** Same gap as above. The SKILL.md was prepended to the prompt, but we can't observe whether the agent invoked any agent-desktop subcommand.
- **Whether the token delta is meaningful.** `meta.agentMeta.usage.total = input + output + cacheRead + cacheWrite`. The smoke-fixture data the parser was modeled on had `cacheRead` at ~97% of `total`. With one run each, no per-component breakdown captured, and OpenClaw cache state varying across runs, the +5.8% delta could be SKILL.md input cost, response-length variance, cache-state difference, or any combination. We can't attribute it.
- **Whether augmented is meaningfully faster than baseline.** With n=1 each and OpenClaw API-call wall-clock varying ±10-20s plausibly, the 11s delta is well within the noise envelope. The earlier draft's "15% faster" headline was statistical garbage. Actual signal requires N≥5 paired runs.
- **What field triggered baseline's `parse_warnings=1`.** The C-C drift detector fires when an unknown field appears in `meta.agentMeta.usage`. We didn't capture the raw transcript, so we cannot identify the field post-facto. The detector's diagnostic value is undermined by not persisting transcripts.
- **Whether the AT-SPI Linux/Wayland horror story we documented in the design doc is real.** The agent never (so far as we know) tried to open gnome-control-center via AT-SPI, so the failure modes around GTK4 a11y degeneracy / wtype / ydotool / sway-only focus were not exercised. The plan said GNOME/X11; we ran on GNOME/Wayland — the whole point of specifying X11 was to enable the input-simulation paths the SKILL.md describes. We didn't reach those paths regardless, so the X11 vs Wayland choice didn't matter for THIS run, but only by accident.

## Methodology gaps to fix before the next run (the M1.5 backlog)

1. **Persist raw stdout/stderr to disk** when running. Either always (gitignored) or at minimum when `parse_warnings > 0`. Without this, drift detection is unactionable and tool-use auditing is impossible.
2. **Capture per-tool-call counts** in the parser. Beyond `screenshots`, also at least: count of `exec` calls, count of `agent-desktop` shell-outs (detectable by argv prefix), count of distinct tool names invoked. Replace the placeholder `screenshots = 0` with real measurement.
3. **Refactor scenario architecture so paired mode runs share a single scenario ID.** Current fork pattern (`a-gnome-settings` + `a-gnome-settings-augmented` as separate directories) means the bench's paired-comparison code path is unused. Options: per-mode prompt overrides in scenario.toml, or a separate "augmentation" config layer.
4. **Run N≥5 paired iterations** before any quantitative claim. Statistical signal floor.
5. **Decide and document** the augmented-prompt strategy. Current SKILL.md text includes "❌ File operations — use shell tools instead" — that actively routes the agent away from AT-SPI for state-mutation tasks like setting a dconf key. If the goal is "force AT-SPI exercise," the prompt should explicitly require it. If the goal is "let the agent pick," the experiment is about agent judgment, not scenario validity. Pick one and acknowledge in the scenario design doc.
6. **Run on GNOME/X11** (per the M1 plan's explicit specification) so the AT-SPI path the SKILL.md describes is actually reachable. The current Wayland run is a deviation from plan.
7. **Author `docs/SCENARIO-DESIGN.md`** documenting the "scenarios that have a CLI shortcut won't differentiate baseline-vs-augmented" constraint. Future scenarios must be CLI-unrouteable for the experiment to mean anything.

## What this run DOES tell us (limited but real)

- **The bench harness end-to-end pipeline runs.** OpenClaw subprocess, `--agent main` selection, JSON parsing, check_state invocation, RunResult emission, report rendering, CSV emission — all worked against real OpenClaw 2026.4.9 output without crashing.
- **OpenClaw `agent --local --message <prompt> --json` is a viable interface for one-shot non-interactive runs.** The smoke-fixture-based parser (`meta.agentMeta.usage.total`) extracted real values from real production output.
- **Both modes succeeded** at producing a state change observable by `check_state.sh`. Whether by GUI or by shell, the scenario completed.
- **The C-C drift detector fired** on a real production output (baseline). The detector's existence is validated; its actionability isn't (per gap #1).
- **Wallclock for one OpenClaw `agent --local` invocation against this prompt + this model + this network** is on the order of 60-72s. Useful planning data for future runs.

## M1 acceptance — honest revision

The earlier draft of this file claimed "M1 fully closed: 9 of 9." That was premature. Honest count:

| check | true status |
|-------|-------------|
| Bench repo public, CI green | ✓ |
| Stub runner works | ✓ |
| OpenClaw runner: baseline mode runs | ✓ but on Wayland not the planned X11; tool-use not captured |
| OpenClaw runner: augmented mode runs | ✓ but same caveats; AT-SPI paths not demonstrably exercised |
| Paired comparison report | ✗ — bench's paired-mode code path was NOT exercised. The two runs used different scenario IDs; the comparison table in this doc was hand-typed |
| SKILL.md drafted + PR'd (#22) | ✓ |
| Issue #21 filed | ✓ |
| Format-flip PR'd (#23) | ✓ |
| HANDOFF | ✓ but reflected the over-claim and was updated separately |

**Realistic M1 status: 6 of 9 fully met; 2 met-with-caveats (single-mode runs each succeeded but the SKILL.md value proposition is uncharacterized); 1 not met (paired report).** M1.5 is needed to actually deliver the empirical artifact the design doc framed.

## Files

- Reports: `reports/live-2026-04-19/{baseline,augmented}/{report.md,report.csv}`
- Augmented scenario fork: `scenarios/a-gnome-settings-augmented/` (acknowledged hack; productize per gap #3)
- Bench HEAD at run time: `34dfa31`
- Tests passing: 63
- CI: green
