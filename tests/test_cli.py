from pathlib import Path

from click.testing import CliRunner

from agent_desktop_evals.cli import main


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
