from __future__ import annotations

from pathlib import Path

import click

from agent_desktop_evals.report import render_csv, render_markdown
from agent_desktop_evals.runner_base import Mode, RunResult
from agent_desktop_evals.runners.openclaw import OpenClawRunner
from agent_desktop_evals.runners.stub import StubRunner
from agent_desktop_evals.scenario import Scenario


def _build_runner(name: str):
    if name == "stub":
        return StubRunner(success=True, tokens=0, screenshots=0, wallclock_s=0.0)
    if name == "openclaw":
        return OpenClawRunner()
    raise click.UsageError(f"unknown runner: {name!r}")


@click.group()
def main() -> None:
    """agent-desktop-evals: paired-baseline benchmark for AI agents on Linux desktops."""


@main.command()
@click.argument("scenario_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--runner", required=True, help="Runner name (stub|openclaw).")
@click.option("--mode", type=click.Choice(["baseline", "augmented"]), required=True)
@click.option("--report-dir", type=click.Path(path_type=Path), default=Path("reports"))
def run(scenario_path: Path, runner: str, mode: str, report_dir: Path) -> None:
    """Run a scenario once and write a report."""
    scenario = Scenario.load(scenario_path)
    r = _build_runner(runner)
    result: RunResult = r.run(scenario, mode=Mode(mode))

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.md").write_text(render_markdown([result]))
    (report_dir / "report.csv").write_text(render_csv([result]))

    summary = (
        f"{scenario.id} {runner} {mode}: "
        f"success={result.success} tokens={result.tokens} "
        f"screenshots={result.screenshots} wallclock={result.wallclock_s:.2f}s"
    )
    if result.tool_calls:
        # Sort by count desc, then by name asc for stable tie-breaking.
        ordered = sorted(result.tool_calls.items(), key=lambda kv: (-kv[1], kv[0]))
        rendered = ",".join(f"{name}:{count}" for name, count in ordered)
        summary += f" tool_calls={rendered}"
    click.echo(summary)
    if result.parse_warnings > 0:
        click.echo(
            f"warning: {result.parse_warnings} transcript event(s) failed validation",
            err=True,
        )
