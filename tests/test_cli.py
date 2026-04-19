from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agent_desktop_evals.cli import main
from agent_desktop_evals.runner_base import Mode, RunResult


def test_cli_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_cli_run_with_stub(scenario_dir: Path, tmp_path: Path):
    runner = CliRunner()
    out_dir = tmp_path / "out"
    result = runner.invoke(main, [
        "run", str(scenario_dir),
        "--runner", "stub",
        "--mode", "baseline",
        "--report-dir", str(out_dir),
    ])
    assert result.exit_code == 0, result.output
    assert (out_dir / "report.md").exists()
    assert (out_dir / "report.csv").exists()


def test_cli_run_unknown_runner_errors(scenario_dir: Path):
    result = CliRunner().invoke(main, [
        "run", str(scenario_dir),
        "--runner", "doesnotexist",
        "--mode", "baseline",
    ])
    assert result.exit_code != 0
    assert "unknown runner" in result.output.lower()


def _fake_result_with_tool_calls(scenario_id: str, tool_calls: dict[str, int]) -> RunResult:
    return RunResult(
        scenario_id=scenario_id,
        runner_name="stub",
        mode=Mode.BASELINE,
        success=True,
        tokens=42,
        screenshots=0,
        wallclock_s=1.5,
        started_at_iso=datetime.now(UTC).isoformat(),
        tool_calls=tool_calls,
    )


def test_cli_run_summary_includes_tool_calls(scenario_dir: Path, tmp_path: Path):
    """When the result has non-empty tool_calls, the one-line summary must
    include them as `tool_calls=name:count,...` sorted by count desc."""
    out_dir = tmp_path / "out"
    fake = _fake_result_with_tool_calls("minimal-scenario",
                                        {"exec": 4, "read": 2})
    with patch("agent_desktop_evals.cli._build_runner") as mock_build:
        mock_build.return_value.run.return_value = fake
        result = CliRunner().invoke(main, [
            "run", str(scenario_dir),
            "--runner", "stub",
            "--mode", "baseline",
            "--report-dir", str(out_dir),
        ])
    assert result.exit_code == 0, result.output
    assert "tool_calls=exec:4,read:2" in result.output, (
        f"expected sorted-by-count-desc tool_calls in summary, got: {result.output!r}"
    )


def test_cli_run_summary_omits_tool_calls_when_empty(scenario_dir: Path, tmp_path: Path):
    """No tool_calls token in the output when the dict is empty."""
    out_dir = tmp_path / "out"
    fake = _fake_result_with_tool_calls("minimal-scenario", {})
    with patch("agent_desktop_evals.cli._build_runner") as mock_build:
        mock_build.return_value.run.return_value = fake
        result = CliRunner().invoke(main, [
            "run", str(scenario_dir),
            "--runner", "stub",
            "--mode", "baseline",
            "--report-dir", str(out_dir),
        ])
    assert result.exit_code == 0, result.output
    assert "tool_calls=" not in result.output, (
        f"empty tool_calls must not appear in summary, got: {result.output!r}"
    )
