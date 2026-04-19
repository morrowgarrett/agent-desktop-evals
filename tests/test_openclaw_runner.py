from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_desktop_evals.runner_base import Mode
from agent_desktop_evals.runners.openclaw import OpenClawRunner, _parse_metrics
from agent_desktop_evals.scenario import Scenario

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SMOKE_FIXTURE = FIXTURE_DIR / "openclaw-smoke-output.txt"

# --- _parse_metrics tests ---


def test_parse_metrics_from_real_openclaw_output():
    """Validate against captured real OpenClaw 2026.4.9 output (smoke test).

    Ground truth: tests/fixtures/openclaw-smoke-output.txt is a real run of
    `openclaw agent --agent main --local --message "Reply with just the word OK" --json`
    captured from stderr. Token counts are at meta.agentMeta.usage.{input,output}
    in that fixture. Sum is 114 + 5 = 119.
    """
    content = SMOKE_FIXTURE.read_text()
    result = _parse_metrics(content)
    assert result["tokens"] == 119, f"got {result}"
    assert result["screenshots"] == 0
    assert result["parse_warnings"] == 0


def test_parse_metrics_handles_empty_transcript():
    """Empty input is valid (no JSON object found) and must not warn — zeros, no noise."""
    metrics = _parse_metrics("")
    assert metrics["tokens"] == 0
    assert metrics["screenshots"] == 0
    assert metrics["parse_warnings"] == 0


def test_parse_metrics_skips_banner_before_json():
    """OpenClaw emits a non-JSON banner line before the JSON object on stderr; parser must
    locate the first `{` and parse from there, ignoring preceding text."""
    transcript = (
        "[plugins] plugins.allow is empty; some chatter here\n"
        '{"meta": {"agentMeta": {"usage": {"input": 100, "output": 50}}}}'
    )
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 150
    assert metrics["parse_warnings"] == 0


def test_parse_metrics_warns_on_unparseable_json():
    """If everything after the first `{` fails to parse, return zeros and warn."""
    transcript = "banner line\n{not valid json at all"
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["screenshots"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_warns_when_usage_missing():
    """A JSON object without meta.agentMeta.usage gets zeros + a parse warning, not a crash."""
    transcript = '{"some_other": "shape"}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_no_brace_in_input():
    """Input with no `{` at all is empty-equivalent: zeros, no warnings."""
    metrics = _parse_metrics("just some banner output\n[plugins] etc")
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 0


def test_parse_metrics_rejects_negative_tokens():
    """Negative token counts are nonsensical; reject and warn."""
    transcript = '{"meta": {"agentMeta": {"usage": {"input": -100, "output": 50}}}}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_rejects_stringified_ints():
    """Token fields must be ints, not strings — silent coercion hides upstream bugs."""
    transcript = '{"meta": {"agentMeta": {"usage": {"input": "100", "output": 50}}}}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


def test_parse_metrics_rejects_floats():
    """Token fields must be ints, not floats — int(100.5) silently truncates to 100."""
    transcript = '{"meta": {"agentMeta": {"usage": {"input": 100.5, "output": 50}}}}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


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


def test_strip_agent_desktop_preserves_leading_empty_segment():
    """POSIX: leading colon means current dir; must not be dropped."""
    result = OpenClawRunner._strip_agent_desktop(":/usr/bin:/bin")
    assert result.startswith(":"), (
        f"expected leading empty segment preserved, got {result!r}"
    )


def test_strip_agent_desktop_preserves_trailing_empty_segment():
    result = OpenClawRunner._strip_agent_desktop("/usr/bin:/bin:")
    assert result.endswith(":"), (
        f"expected trailing empty segment preserved, got {result!r}"
    )


def test_strip_agent_desktop_preserves_double_colon():
    result = OpenClawRunner._strip_agent_desktop("/usr/bin::/bin")
    assert "::" in result, f"expected '::' preserved, got {result!r}"


def test_strip_agent_desktop_uses_executable_check_not_path_exists(tmp_path: Path):
    """A directory named 'agent-desktop' should NOT cause the parent to be stripped."""
    parent = tmp_path / "with_dir_named_agent_desktop"
    parent.mkdir()
    (parent / "agent-desktop").mkdir()  # directory, not file
    result = OpenClawRunner._strip_agent_desktop(str(parent))
    assert str(parent) in result, (
        f"directory named 'agent-desktop' should not trigger strip; got {result!r}"
    )


# --- OpenClawRunner.run tests (mocked subprocess) ---


def _agent_mock(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _stderr_blob(input_tokens: int, output_tokens: int) -> str:
    """Build a stderr blob shaped like the real OpenClaw output."""
    body = json.dumps(
        {"meta": {"agentMeta": {"usage": {"input": input_tokens, "output": output_tokens}}}}
    )
    return f"[plugins] banner line goes here\n{body}\n"


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_happy_path(mock_run, scenario_dir: Path, fake_openclaw_on_path):
    """Agent succeeds AND check_state passes → success=True."""
    agent = _agent_mock(stdout="", stderr=_stderr_blob(500, 100))
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
    agent = _agent_mock(stdout="", stderr=_stderr_blob(50, 10))
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
    """Runner must invoke `openclaw agent --agent <id> --local --message <prompt> --json`,
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
def test_openclaw_runner_passes_agent_flag_default_main(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """OpenClaw bails without --agent; runner must default to 'main' (the user's default agent)."""
    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]
    OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    agent_args = mock_run.call_args_list[0].args[0]
    assert "--agent" in agent_args, f"missing --agent flag: {agent_args!r}"
    # --agent must be immediately followed by the agent id
    assert agent_args[agent_args.index("--agent") + 1] == "main"


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_agent_id_is_configurable(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """The --agent value must be configurable via constructor for non-default agents."""
    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]
    OpenClawRunner(agent_id="custom-agent").run(
        Scenario.load(scenario_dir), mode=Mode.AUGMENTED
    )
    agent_args = mock_run.call_args_list[0].args[0]
    assert agent_args[agent_args.index("--agent") + 1] == "custom-agent"


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_parses_stderr_not_stdout(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """OpenClaw emits its JSON on STDERR, not stdout. Runner must parse from stderr.

    Regression: previous implementation read proc.stdout, which is empty for openclaw,
    causing tokens to silently be 0 on every real run.
    """
    fixture = SMOKE_FIXTURE.read_text()
    agent_call = MagicMock(returncode=0, stdout="", stderr=fixture)
    check_call = MagicMock(returncode=0)
    mock_run.side_effect = [agent_call, check_call]
    result = OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tokens > 0, (
        f"expected tokens > 0 from real fixture, got {result.tokens}"
    )
    assert result.tokens == 119  # 114 input + 5 output from the fixture
    assert result.success is True


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_falls_back_to_stdout_if_stderr_empty(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """If openclaw ever changes which stream it emits on, parsing should not regress.

    The runner combines stdout+stderr through _parse_metrics so the metrics still
    surface either way."""
    body = _stderr_blob(200, 50)
    agent_call = MagicMock(returncode=0, stdout=body, stderr="")
    check_call = MagicMock(returncode=0)
    mock_run.side_effect = [agent_call, check_call]
    result = OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tokens == 250


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
    agent = _agent_mock(stdout="", stderr=_stderr_blob(100, 50))
    mock_run.side_effect = [
        agent,
        subprocess.TimeoutExpired(cmd="bash", timeout=30),
    ]

    scenario = Scenario.load(scenario_dir)
    result = OpenClawRunner().run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.error is not None
    assert "check_state timed out" in result.error.lower()
