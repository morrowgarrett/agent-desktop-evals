from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_desktop_evals.runner_base import Mode
from agent_desktop_evals.runners.openclaw import OpenClawRunner, _parse_metrics
from agent_desktop_evals.scenario import Scenario

# --- _parse_metrics tests ---


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


def test_parse_metrics_tolerates_null_token_fields():
    """A null token value must degrade to zero, not raise — plan-level promise."""
    transcript = '{"event": "turn_complete", "input_tokens": null, "output_tokens": 50}'
    assert _parse_metrics(transcript) == {"tokens": 50, "screenshots": 0}


# --- _strip_agent_desktop tests ---


def test_strip_agent_desktop_removes_dirs_with_binary(tmp_path: Path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    fake_bin = b / "agent-desktop"
    fake_bin.touch()
    fake_bin.chmod(0o755)
    result = OpenClawRunner._strip_agent_desktop(f"{a}:{b}:/usr/bin")
    assert str(a) in result
    assert str(b) not in result
    assert "/usr/bin" in result


def test_strip_agent_desktop_handles_empty_path():
    assert OpenClawRunner._strip_agent_desktop("") == ""


def test_strip_agent_desktop_skips_empty_segments():
    """A leading/trailing/double colon shouldn't crash."""
    result = OpenClawRunner._strip_agent_desktop(":/usr/bin::/bin:")
    assert "/usr/bin" in result
    assert "/bin" in result


# --- OpenClawRunner.run tests (mocked subprocess) ---


def _agent_mock(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_happy_path(mock_run, scenario_dir: Path):
    """Agent succeeds AND check_state passes → success=True."""
    agent = _agent_mock(
        stdout='{"event": "turn_complete", "input_tokens": 500, "output_tokens": 100}\n'
    )
    check = _agent_mock(returncode=0)
    mock_run.side_effect = [agent, check]

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is True
    assert result.tokens == 600
    assert result.screenshots == 0
    assert mock_run.call_count == 2

    # Verify the second call invoked the check script
    check_call_args = mock_run.call_args_list[1].args[0]
    assert check_call_args[0] == "bash"
    assert check_call_args[1] == str(scenario.check_script)


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_check_failure_means_success_false(mock_run, scenario_dir: Path):
    """Agent succeeds but check_state returns non-expected exit → success=False."""
    agent = _agent_mock(
        stdout='{"event": "turn_complete", "input_tokens": 50, "output_tokens": 10}'
    )
    check = _agent_mock(returncode=1)  # scenario expects 0
    mock_run.side_effect = [agent, check]

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.tokens == 60


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_baseline_strips_agent_desktop_from_path(
    mock_run, scenario_dir: Path, tmp_path: Path, monkeypatch
):
    """BASELINE mode must invoke the agent subprocess with PATH stripped of agent-desktop dirs."""
    # Set up a fake PATH with one dir containing agent-desktop and one without
    has_ad = tmp_path / "with_ad"
    has_ad.mkdir()
    fake_ad = has_ad / "agent-desktop"
    fake_ad.touch()
    fake_ad.chmod(0o755)
    no_ad = tmp_path / "without_ad"
    no_ad.mkdir()
    monkeypatch.setenv("PATH", f"{has_ad}:{no_ad}")

    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]

    scenario = Scenario.load(scenario_dir)
    OpenClawRunner().run(scenario, mode=Mode.BASELINE)

    # First call (agent) should have stripped PATH
    agent_env = mock_run.call_args_list[0].kwargs["env"]
    assert str(has_ad) not in agent_env["PATH"]
    assert str(no_ad) in agent_env["PATH"]


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_augmented_keeps_path_intact(
    mock_run, scenario_dir: Path, tmp_path: Path, monkeypatch
):
    """AUGMENTED mode must NOT strip PATH."""
    has_ad = tmp_path / "with_ad"
    has_ad.mkdir()
    fake_ad = has_ad / "agent-desktop"
    fake_ad.touch()
    fake_ad.chmod(0o755)
    monkeypatch.setenv("PATH", str(has_ad))

    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]

    scenario = Scenario.load(scenario_dir)
    OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    agent_env = mock_run.call_args_list[0].kwargs["env"]
    assert str(has_ad) in agent_env["PATH"]


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_timeout_returns_failure_result(mock_run, scenario_dir: Path):
    """subprocess.TimeoutExpired → RunResult with success=False, error mentions timeout."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="openclaw", timeout=30)

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.tokens == 0
    assert result.screenshots == 0
    assert result.error is not None
    assert "timeout" in result.error.lower()
