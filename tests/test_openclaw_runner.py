from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_desktop_bench.runner_base import Mode
from agent_desktop_bench.runners.openclaw import OpenClawRunner, _parse_metrics
from agent_desktop_bench.scenario import Scenario


def test_parse_metrics_extracts_token_and_screenshot_counts():
    transcript = '''
    {"event": "turn_complete", "input_tokens": 1200, "output_tokens": 340}
    {"event": "tool_call", "tool": "screenshot", "result_bytes": 48000}
    {"event": "tool_call", "tool": "screenshot", "result_bytes": 51200}
    {"event": "turn_complete", "input_tokens": 1800, "output_tokens": 220}
    '''
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 1200 + 340 + 1800 + 220
    assert metrics["screenshots"] == 2


def test_parse_metrics_handles_empty_transcript():
    assert _parse_metrics("") == {"tokens": 0, "screenshots": 0}


def test_parse_metrics_ignores_unstructured_lines():
    transcript = '''
    Welcome to OpenClaw
    {"event": "turn_complete", "input_tokens": 100, "output_tokens": 50}
    Some warning logged here
    '''
    assert _parse_metrics(transcript)["tokens"] == 150


@patch("agent_desktop_bench.runners.openclaw.subprocess.run")
def test_openclaw_runner_invokes_subprocess(mock_run, scenario_dir: Path):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"event": "turn_complete", "input_tokens": 500, "output_tokens": 100}\n',
        stderr="",
    )
    scenario = Scenario.load(scenario_dir)
    runner = OpenClawRunner()
    result = runner.run(scenario, mode=Mode.BASELINE)
    assert result.success is True
    assert result.tokens == 600
    assert result.screenshots == 0
    assert mock_run.called
