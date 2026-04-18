from __future__ import annotations

import csv
import io
from collections import defaultdict

from agent_desktop_bench.runner_base import Mode, RunResult


def render_markdown(results: list[RunResult]) -> str:
    """Render a paired-mode comparison report."""
    if not results:
        return "# No results\n"

    by_pair: dict[tuple[str, str], dict[Mode, RunResult]] = defaultdict(dict)
    for r in results:
        by_pair[(r.scenario_id, r.runner_name)][r.mode] = r

    lines: list[str] = ["# Benchmark report", ""]
    for (scenario, runner), modes in sorted(by_pair.items()):
        lines.append(f"## {scenario} × {runner}")  # noqa: RUF001  # multiplication sign is intentional
        lines.append("")
        lines.append("| mode | success | tokens | screenshots | wall-clock (s) |")
        lines.append("|------|---------|--------|-------------|----------------|")
        for mode in (Mode.BASELINE, Mode.AUGMENTED):
            if mode in modes:
                r = modes[mode]
                lines.append(
                    f"| {mode.value} | {'✓' if r.success else '✗'} | {r.tokens} "
                    f"| {r.screenshots} | {r.wallclock_s:.2f} |"
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
                f"| {(b.wallclock_s - a.wallclock_s):+.2f} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_csv(results: list[RunResult]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "scenario_id", "runner_name", "mode", "success",
        "tokens", "screenshots", "wallclock_s", "steps", "started_at_iso",
    ])
    for r in results:
        writer.writerow([
            r.scenario_id, r.runner_name, r.mode.value, r.success,
            r.tokens, r.screenshots, f"{r.wallclock_s:.3f}", r.steps, r.started_at_iso,
        ])
    return buf.getvalue()
