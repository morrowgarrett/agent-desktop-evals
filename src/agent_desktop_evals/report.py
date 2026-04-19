from __future__ import annotations

import csv
import io
from collections import defaultdict

from agent_desktop_evals.runner_base import Mode, RunResult


def _format_tool_calls(tool_calls: dict[str, int]) -> str:
    """Render tool_calls dict as 'name1:N1,name2:N2,...' sorted by count desc.

    Ties on count are broken by name ascending. Empty dict returns "".
    Shared by report.py and cli.py to keep a single source of truth for the
    on-disk and on-stdout format.
    """
    if not tool_calls:
        return ""
    items = sorted(tool_calls.items(), key=lambda kv: (-kv[1], kv[0]))
    return ",".join(f"{n}:{c}" for n, c in items)


def render_markdown(results: list[RunResult]) -> str:
    """Render a paired-mode comparison report."""
    if not results:
        return "# No results\n"

    by_pair: dict[tuple[str, str], dict[Mode, RunResult]] = defaultdict(dict)
    collisions: list[tuple[str, str, Mode]] = []
    for r in results:
        key = (r.scenario_id, r.runner_name)
        if r.mode in by_pair[key]:
            collisions.append((r.scenario_id, r.runner_name, r.mode))
        by_pair[key][r.mode] = r

    lines: list[str] = ["# Benchmark report", ""]
    for (scenario, runner), modes in sorted(by_pair.items()):
        lines.append(f"## {scenario} × {runner}")  # noqa: RUF001  # multiplication sign is intentional
        lines.append("")
        lines.append("| mode | success | tokens | screenshots | tool calls | wall-clock (s) |")
        lines.append("|------|---------|--------|-------------|------------|----------------|")
        for mode in (Mode.BASELINE, Mode.AUGMENTED):
            if mode in modes:
                r = modes[mode]
                tc = _format_tool_calls(r.tool_calls) or "—"  # em-dash for empty
                lines.append(
                    f"| {mode.value} | {'✓' if r.success else '✗'} | {r.tokens} "
                    f"| {r.screenshots} | {tc} | {r.wallclock_s:.2f} |"
                )
        if Mode.BASELINE in modes and Mode.AUGMENTED in modes:
            b, a = modes[Mode.BASELINE], modes[Mode.AUGMENTED]
            tok_savings = (
                f"{(1 - a.tokens / b.tokens) * 100:.1f}%" if b.tokens else "n/a"
            )
            shot_savings = (
                f"{(1 - a.screenshots / b.screenshots) * 100:.1f}%" if b.screenshots else "n/a"
            )
            lines.append(
                f"| **delta (savings)** | — | {tok_savings} | {shot_savings} "
                f"| — | {(b.wallclock_s - a.wallclock_s):+.2f} |"
            )
        lines.append("")

    if collisions:
        lines.append("## Duplicate result warnings")
        lines.append("")
        lines.append(
            "The following (scenario, runner, mode) triples appeared more than "
            "once. Only the LAST value is shown above; earlier values were "
            "overwritten:"
        )
        lines.append("")
        for scenario, runner, mode in collisions:
            lines.append(f"- `{scenario}` x `{runner}` x `{mode.value}`")
        lines.append("")

    return "\n".join(lines)


def render_csv(results: list[RunResult]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "scenario_id", "runner_name", "mode", "success",
        "tokens", "screenshots", "tool_calls", "wallclock_s", "steps", "started_at_iso",
    ])
    for r in results:
        writer.writerow([
            r.scenario_id, r.runner_name, r.mode.value, r.success,
            r.tokens, r.screenshots, _format_tool_calls(r.tool_calls),
            f"{r.wallclock_s:.3f}", r.steps, r.started_at_iso,
        ])
    return buf.getvalue()
