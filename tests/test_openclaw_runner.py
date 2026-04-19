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
    metrics = _parse_metrics("")
    assert metrics["tokens"] == 0
    assert metrics["screenshots"] == 0
    assert metrics["parse_warnings"] == 0


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
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 50
    assert metrics["screenshots"] == 0


def test_parse_metrics_rejects_negative_tokens():
    """Negative token counts are nonsensical; reject the event and warn."""
    transcript = '{"event": "turn_complete", "input_tokens": -100, "output_tokens": 50}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_rejects_stringified_ints():
    """Token fields must be ints, not strings — silent coercion hides upstream bugs."""
    transcript = '{"event": "turn_complete", "input_tokens": "100", "output_tokens": 50}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_rejects_floats():
    """Token fields must be ints, not floats — int(100.5) silently truncates to 100."""
    transcript = '{"event": "turn_complete", "input_tokens": 100.5, "output_tokens": 50}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_handles_pretty_printed_json():
    """Multi-line indented JSON should either parse correctly or surface a parse_warning,
    not silently drop the event."""
    transcript = (
        "{\n"
        '  "event": "turn_complete",\n'
        '  "input_tokens": 100,\n'
        '  "output_tokens": 50\n'
        "}"
    )
    metrics = _parse_metrics(transcript)
    # Either tokens parse correctly (preferred), or the event is detected and
    # a parse_warning is recorded — but the event must not be silently lost.
    assert metrics["tokens"] == 150 or metrics["parse_warnings"] >= 1, (
        f"pretty-printed JSON silently dropped: {metrics}"
    )


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
def test_openclaw_runner_happy_path(mock_run, scenario_dir: Path, fake_openclaw_on_path):
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
def test_openclaw_runner_check_failure_means_success_false(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
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
    # Set up a fake PATH with one dir containing agent-desktop and one without.
    # Also stage an openclaw binary so OpenClawRunner can resolve it at init.
    has_ad = tmp_path / "with_ad"
    has_ad.mkdir()
    fake_ad = has_ad / "agent-desktop"
    fake_ad.touch()
    fake_ad.chmod(0o755)
    no_ad = tmp_path / "without_ad"
    no_ad.mkdir()
    oc_bin = no_ad / "openclaw"
    oc_bin.write_text("#!/bin/bash\nexit 0\n")
    oc_bin.chmod(0o755)
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
    oc_bin = has_ad / "openclaw"
    oc_bin.write_text("#!/bin/bash\nexit 0\n")
    oc_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(has_ad))

    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]

    scenario = Scenario.load(scenario_dir)
    OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    agent_env = mock_run.call_args_list[0].kwargs["env"]
    assert str(has_ad) in agent_env["PATH"]


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_timeout_returns_failure_result(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """subprocess.TimeoutExpired → RunResult with success=False, error mentions timeout."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="openclaw", timeout=30)

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.tokens == 0
    assert result.screenshots == 0
    assert result.error is not None
    assert "timeout" in result.error.lower()


def test_openclaw_runner_handles_missing_binary(scenario_dir: Path):
    """If the openclaw binary doesn't exist, return a failure RunResult, not raise."""
    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner(openclaw_bin="/nonexistent/path/openclaw").run(
        scenario, mode=Mode.AUGMENTED
    )
    assert result.success is False
    assert result.error is not None
    assert "failed to spawn" in result.error.lower()


def test_baseline_mode_still_finds_openclaw_when_co_located(
    scenario_dir: Path, tmp_path: Path, monkeypatch
):
    """If openclaw and agent-desktop live in the same directory (e.g. ~/.local/bin),
    BASELINE-mode PATH stripping must not prevent the spawn from finding openclaw.

    Resolution: OpenClawRunner resolves the binary to an absolute path at __init__
    via shutil.which, so the spawn call is unaffected by PATH manipulation.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    openclaw_bin = bindir / "openclaw"
    openclaw_bin.write_text("#!/bin/bash\necho '{}'\nexit 0\n")
    openclaw_bin.chmod(0o755)
    ad_bin = bindir / "agent-desktop"
    ad_bin.touch()
    ad_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))

    runner = OpenClawRunner(openclaw_bin="openclaw")
    scenario = Scenario.load(scenario_dir)

    with patch("agent_desktop_evals.runners.openclaw.subprocess.run") as mock_run:
        mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]
        result = runner.run(scenario, mode=Mode.BASELINE)

    # The spawn call must use the absolute path resolved at init,
    # not the bare name (which would fail to be found after PATH-strip).
    spawn_args = mock_run.call_args_list[0].args[0]
    assert spawn_args[0] == str(openclaw_bin), (
        f"expected absolute path {openclaw_bin}, got {spawn_args[0]}"
    )
    assert "failed to spawn" not in (result.error or "")


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_uses_agent_subcommand(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """Runner must invoke `openclaw agent --local --message <prompt> --json`,
    not the nonexistent `chat --print --json`."""
    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]
    OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    agent_call_args = mock_run.call_args_list[0].args[0]
    assert agent_call_args[1] == "agent", (
        f"expected 'agent' subcommand, got {agent_call_args!r}"
    )
    assert "--local" in agent_call_args
    assert "--message" in agent_call_args
    assert "--json" in agent_call_args
    # Old wrong invocation should be absent
    assert "chat" not in agent_call_args
    assert "--print" not in agent_call_args


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_nonzero_agent_exit_forces_failure_even_if_check_passes(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """If the agent process fails but check_state happens to pass, success must be False."""
    agent_failed = _agent_mock(
        returncode=2, stderr="agent: unknown subcommand 'chat'", stdout=""
    )
    check_passed = _agent_mock(returncode=0)  # scenario.expect_exit_code is 0
    mock_run.side_effect = [agent_failed, check_passed]
    result = OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.success is False, "agent failure must override passing check"
    assert result.error is not None
    assert "agent" in result.error.lower()  # error should attribute the failure to agent


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_check_timeout(mock_run, scenario_dir: Path, fake_openclaw_on_path):
    """If the check_state subprocess times out, surface error mentioning check_state timeout."""
    agent = _agent_mock(
        stdout='{"event": "turn_complete", "input_tokens": 100, "output_tokens": 50}'
    )
    mock_run.side_effect = [
        agent,
        subprocess.TimeoutExpired(cmd="bash", timeout=30),
    ]

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.error is not None
    assert "check_state timed out" in result.error.lower()
