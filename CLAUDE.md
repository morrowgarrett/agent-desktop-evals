# CLAUDE.md — agent-desktop-evals

## Project context

Reproducible evals for [agent-desktop](https://github.com/crowecawcaw/agent-desktop) — Stephen Crowe's accessibility-first Rust CLI for AI agents to control Linux desktops. Paired-baseline benchmark: run scenarios with vs without agent-desktop on PATH, capture token / screenshot / wallclock metrics, compare.

Scenarios are stored as markdown (frontmatter + prompt + expected outcome + verification + reset) and consumed by either:

- **This Python harness** (formal benchmark, paired-baseline) at `morrowgarrett/agent-desktop-evals`
- **Stephen's in-repo `evals/` directory** at https://github.com/crowecawcaw/agent-desktop/pull/22 (lightweight regression suite)

The two layers share the markdown scenario format. Same source of truth, different consumers.

## Workflow rules (mandatory)

### 1. Codex review before shipping non-trivial code

Any non-trivial change ships only after a Codex review pass.

```bash
# Run from a fresh terminal, NOT inside Claude Code.
# (bubblewrap sandbox blocks Codex CLI from inside CC sessions.)
codex exec "review the diff in <path>; find bugs, weak assertions, and tautological tests"
```

**Why this rule exists:** Codex catches test-quality bugs (tautological assertions, weak existence checks, tests that pass for the wrong reasons) that the in-session subagent + review pipeline reliably misses. The format-flip PR's first test asserted only "stdout doesn't start with `<`" — would have passed for empty stdout. Codex caught it. After that finding, an adversarial Claude review of this bench harness found 22 more issues including 5 critical bugs.

### 2. Adversarial Claude review when Codex unavailable

When Codex isn't viable (sandbox blocked, time pressure, narrow scope), dispatch a `superpowers:code-reviewer` subagent with **explicit adversarial framing**:

> Find bugs the implementer missed. Do not validate the work. Assume the diff is wrong until proven otherwise.

Less effective than Codex but catches the obvious failure modes.

### 3. Sequential subagents on this repo, never parallel

Concurrent subagents on the same git repo cause checkout/stash collisions. **One bench-harness change at a time.** If you need to do two things, queue them.

### 4. Verify branch state before subagent dispatch

Subagents that read files assume the working tree matches some expectation. Before dispatch, confirm:

```bash
git branch --show-current
git status -s
```

A subagent that runs against an unexpected branch or a dirty tree wastes a review cycle and produces misleading findings.

### 5. TDD remains rigorous

Red-green discipline. Watch for:

- **Tautological-pass tests**: `assert X or Y` where `Y` is always true.
- **Self-mocking tests**: tests that mock the same thing they claim to test.
- **Wrong-reason passes**: tests that succeed because of an unrelated default, not because the behavior is correct.

These are explicitly part of the code-quality review focus, not just style nits.

## Environment

- Python 3.12, `uv`-managed
- pydantic 2, click 8, pytest 9, ruff
- Linux-first development (Ubuntu 24.04, GNOME 46.7); other display servers covered by Phase 2

## CI

GitHub Actions runs ruff + pytest on Linux. **No `agent-desktop` binary in CI** — the subprocess interface is mocked via fixture. Live runs are local-only.

## Stephen's repo conventions (relevant when contributing back)

When opening a PR or issue against `crowecawcaw/agent-desktop`:

- **Conventional commit prefixes**: `fix(linux):`, `docs(skills):`, `feat(cli):`
- **Issue first, PR second** (per his contributor docs)
- **Small focused PRs** — anything >200 lines should land an issue first
- **TDD discipline** — tests gate merges
- **Agent-first design** — JSON-out by default; treat humans as a fallback consumer, not the primary

## Related context

- Cross-repo handoff (when this work pauses): `~/projects/xa11y/xa11y-contrib/HANDOFF-agent-desktop-evals.md`
- Sibling xa11y-core track: `~/projects/xa11y/xa11y-contrib/HANDOFF.md`
