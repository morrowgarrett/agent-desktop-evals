from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_desktop_evals.runner_base import Mode, RunResult
from agent_desktop_evals.runners.openclaw import OpenClawRunner, _parse_metrics
from agent_desktop_evals.scenario import Scenario

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SMOKE_FIXTURE = FIXTURE_DIR / "openclaw-smoke-output.txt"


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-chdir each test to its tmp_path so OpenClawRunner's default
    transcript-persistence path (./reports/raw/) writes inside the test sandbox
    instead of polluting the real repo's reports/raw/ directory.

    Tests that pass an explicit absolute transcript_dir or transcript_dir=None
    are unaffected. Tests that require a specific cwd should call
    monkeypatch.chdir themselves AFTER this fixture runs.
    """
    monkeypatch.chdir(tmp_path)

# --- _parse_metrics tests ---


def test_parse_metrics_from_real_openclaw_output():
    """Validate against captured real OpenClaw 2026.4.9 output (smoke test).

    Ground truth: tests/fixtures/openclaw-smoke-output.txt is a real run of
    `openclaw agent --agent main --local --message "Reply with just the word OK" --json`
    captured from stderr. Token counts are at meta.agentMeta.usage.total
    (the precomputed cumulative accumulator) in that fixture. Real value: 148727.
    Previously the parser only summed input + output (= 119) and silently dropped
    cacheRead and cacheWrite, undercounting by three orders of magnitude.
    """
    content = SMOKE_FIXTURE.read_text()
    result = _parse_metrics(content)
    assert result["tokens"] == 148727, f"got {result}"
    assert result["screenshots"] == 0
    assert result["parse_warnings"] == 0


def test_parse_metrics_prefers_total_field():
    """When 'total' is present in usage, use it as authoritative.

    OpenClaw's createUsageAccumulator precomputes total = input+output+cacheRead+cacheWrite;
    we prefer that over re-summing to avoid drift if the upstream definition changes.
    """
    transcript = (
        '{"meta": {"agentMeta": {"usage": '
        '{"input": 114, "output": 5, "cacheRead": 148608, "total": 148727}}}}'
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 148727, f"expected 148727, got {result}"
    assert result["parse_warnings"] == 0


def test_parse_metrics_sums_all_four_when_total_missing():
    """If total is absent, sum input + output + cacheRead + cacheWrite."""
    transcript = (
        '{"meta": {"agentMeta": {"usage": '
        '{"input": 100, "output": 50, "cacheRead": 1000, "cacheWrite": 200}}}}'
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 1350, f"expected 1350, got {result}"
    assert result["parse_warnings"] == 0


def test_parse_metrics_handles_trailing_log_after_json():
    """Log line after the JSON object should not zero the parse."""
    transcript = (
        '{"meta": {"agentMeta": {"usage": '
        '{"input": 100, "output": 50, "total": 150}}}}\n'
        "[shutdown] cleanup complete"
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 150, f"trailing log should not break parse, got {result}"
    assert result["parse_warnings"] == 0


def test_parse_metrics_skips_banner_with_brace():
    """Banner containing '{' should not derail the parser.

    Previous strategy of `text.find('{')` would land inside the banner and fail.
    """
    transcript = (
        "[plugins] loaded { 1 item } warning ok\n"
        '{"meta": {"agentMeta": {"usage": {"input": 10, "output": 5, "total": 15}}}}'
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 15
    assert result["parse_warnings"] == 0


def test_parse_metrics_handles_multiple_json_objects():
    """Multiple JSON objects: last-cumulative-wins (NOT sum).

    OpenClaw's mergeUsageIntoAccumulator (pi-embedded-Vw-lS5ti.js:32847) populates
    usage via `target.input += usage.input` — every emission of agentMeta carries
    the running cumulative usageAccumulator, not a per-event delta. Summing across
    objects would N-count tokens for any future stream / retry / error-path
    multi-emit shape. Take the last cumulative total instead.
    """
    transcript = (
        '{"meta": {"agentMeta": {"usage": {"input": 100, "output": 50, "total": 150}}}}'
        "\n"
        '{"meta": {"agentMeta": {"usage": {"input": 200, "output": 100, "total": 300}}}}'
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 300, (
        f"expected last-cumulative 300, NOT sum 450 (cumulative semantics), got {result}"
    )
    assert result["parse_warnings"] == 0


def test_parse_metrics_handles_wrapped_in_noise():
    """JSON wrapped by non-JSON noise on both sides should still parse."""
    transcript = (
        "Logging started\n"
        '{"meta": {"agentMeta": {"usage": {"input": 10, "output": 5, "total": 15}}}}\n'
        "Logging ended"
    )
    result = _parse_metrics(transcript)
    assert result["tokens"] == 15
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


def test_parse_metrics_corrupted_json_warns_not_silently_zeros():
    """If a `{` appears in input but every raw_decode attempt from each `{` fails,
    that's corruption — operator must get a parse_warning signal.

    Reverses the prior behavior. The earlier 'silently zero' policy made
    corrupted streams (e.g., U+FFFD landed inside a JSON value via
    errors='replace') indistinguishable from a no-run case. Now: at least one
    `{` present + zero successfully-decoded objects ⇒ parse_warnings >= 1.
    """
    # Repro of the U+FFFD corruption case from the review.
    transcript = '{"meta":{"agentMeta":{"usage":{"input":1\ufffd00,"output":50}}}}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["screenshots"] == 0
    assert metrics["parse_warnings"] >= 1, (
        f"corruption must surface as warning, got {metrics}"
    )

    # Original "banner line\n{not valid json at all" — also corruption, also warns.
    transcript2 = "banner line\n{not valid json at all"
    metrics2 = _parse_metrics(transcript2)
    assert metrics2["tokens"] == 0
    assert metrics2["parse_warnings"] >= 1, (
        f"a `{{` that fails to decode must warn, got {metrics2}"
    )


def test_parse_metrics_pure_noise_with_no_brace_does_not_warn():
    """Pure noise (no `{` in input at all) is empty-equivalent: zeros, no warnings.

    This carves the legitimate 'no JSON ever' case out of the corruption path:
    no `{` ⇒ never even attempted a decode ⇒ no warning to emit.
    """
    metrics = _parse_metrics("just banner text\n[plugins] no braces here\n")
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 0


def test_parse_metrics_warns_when_usage_missing():
    """A valid JSON object without meta.agentMeta.usage gets zeros + a parse warning.

    We did successfully parse a JSON object — its shape is just wrong. That's
    schema drift and must surface as a warning.
    """
    transcript = '{"some_other": "shape"}'
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] == 1


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


def test_parse_metrics_tolerates_unknown_usage_fields_with_warning():
    """Schema drift: a future OpenClaw adding an unknown key (e.g., reasoning_tokens
    for o-series / gpt-5 reasoning models) must NOT hard-zero the run.

    extra='allow' on the _Usage model lets the known fields through; the parser
    surfaces the drift as a parse_warning rather than treating the whole run as
    corrupt. This is a publication-blocker if missed: an OpenClaw release with a
    new usage field would otherwise silently report tokens=0 across every run.
    """
    transcript = (
        '{"meta": {"agentMeta": {"usage": '
        '{"input": 100, "output": 50, "total": 150, "reasoning": 500}}}}'
    )
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 150, (
        f"unknown usage field must NOT zero known counts, got {metrics}"
    )
    assert metrics["parse_warnings"] >= 1, (
        f"unknown usage field must surface as drift warning, got {metrics}"
    )


def test_parse_metrics_total_zero_with_nonzero_components_falls_through():
    """Defensive: if upstream ever buggily emits total=0 alongside nonzero components,
    truthy fall-through to the sum self-heals. (Pure-truthy check on usage.total.)
    """
    transcript = (
        '{"meta": {"agentMeta": {"usage": '
        '{"input": 100, "output": 50, "cacheRead": 200, "cacheWrite": 0, "total": 0}}}}'
    )
    metrics = _parse_metrics(transcript)
    assert metrics["tokens"] == 350, (
        f"total=0 with nonzero components must fall through to component sum, got {metrics}"
    )


def test_parse_metrics_bounds_pathological_brace_input():
    """Adversarial input of many stray `{` must not hang on O(N^2) decode attempts.

    100K stray braces should complete fast and produce a single parse_warning
    when the bound is hit (per the 'failed_decodes > 0 and no objects' branch).
    """
    transcript = "{" * 100_000
    import time as _time
    t0 = _time.monotonic()
    metrics = _parse_metrics(transcript)
    elapsed = _time.monotonic() - t0
    # Bound is 10000 attempts; each empty raw_decode is microseconds. Allow generous slack.
    assert elapsed < 5.0, f"bounded loop must complete fast; took {elapsed:.2f}s"
    assert metrics["tokens"] == 0
    assert metrics["parse_warnings"] >= 1, (
        f"pathological corruption must surface a warning, got {metrics}"
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
def test_parses_concatenated_streams_not_stdout_only(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """Verified against OpenClaw source: register.agent-DSGqePoo.js:95 →
    writeRuntimeJson → runtime-BXaxArxm.js:21 writeStdout → process.stdout.write.
    JSON goes to STDOUT; the colored '[plugins]' banner from console.error goes
    to stderr. Our smoke fixture is a `2>&1` capture interleaving them.

    The runner concatenates BOTH streams through _parse_metrics so it captures
    the JSON regardless of which stream emits it. This test pins the dual-stream
    behavior by placing the real fixture on stderr (legacy regression shape) and
    a sentinel on stdout — fixture's tokens must still reach the result.
    """
    fixture = SMOKE_FIXTURE.read_text()
    sentinel_stdout = "[plugins] noise on stdout that contains no usage block\n"
    agent_call = MagicMock(returncode=0, stdout=sentinel_stdout, stderr=fixture)
    check_call = MagicMock(returncode=0)
    mock_run.side_effect = [agent_call, check_call]
    result = OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    # 148727 = real fixture's precomputed total (input 114 + output 5 + cacheRead 148608).
    assert result.tokens == 148727, (
        f"expected 148727 tokens from concatenated streams, got {result.tokens}"
    )
    assert result.success is True


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_parses_concatenated_streams_with_fixture_on_stdout(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """Mirror of the above: real OpenClaw writes JSON to STDOUT (the smoke
    fixture is a 2>&1 capture). Place the fixture on stdout, banner sentinel on
    stderr — tokens must still extract. Together with the previous test this
    catches a regression in either direction (stdout-only or stderr-only).
    """
    fixture = SMOKE_FIXTURE.read_text()
    sentinel_stderr = "[plugins] noise on stderr that contains no usage block\n"
    agent_call = MagicMock(returncode=0, stdout=fixture, stderr=sentinel_stderr)
    check_call = MagicMock(returncode=0)
    mock_run.side_effect = [agent_call, check_call]
    result = OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tokens == 148727, (
        f"expected 148727 tokens from stdout fixture (real OpenClaw shape), "
        f"got {result.tokens}"
    )
    assert result.success is True


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


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_uses_errors_replace_for_subprocess_decoding(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """Both subprocess.run calls must pass errors='replace'.

    Default text=True uses errors='strict', which raises UnicodeDecodeError on
    any non-UTF8 byte in subprocess output. That used to crash the runner
    mid-eval because the existing exception handler didn't catch ValueError
    subclasses outside FileNotFoundError/PermissionError/OSError.
    """
    mock_run.side_effect = [_agent_mock(stdout=""), _agent_mock(returncode=0)]
    OpenClawRunner().run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)

    for call in mock_run.call_args_list:
        assert call.kwargs.get("errors") == "replace", (
            f"subprocess.run called without errors='replace': {call.kwargs!r}"
        )


# --- Transcript persistence tests ---


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_persists_transcript_to_disk(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """When transcript_dir is set, raw stderr+stdout written to a path on disk."""
    transcript_dir = tmp_path / "raw"
    runner = OpenClawRunner(transcript_dir=transcript_dir)

    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="agent stdout content", stderr="agent stderr content"),
        MagicMock(returncode=0, stdout="check stdout", stderr=""),
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.transcript_path is not None
    p = Path(result.transcript_path)
    assert p.is_absolute(), f"transcript_path must be absolute, got {result.transcript_path!r}"
    assert p.exists()
    content = p.read_text()
    assert "agent stderr content" in content
    assert "agent stdout content" in content
    assert "scenario_id: minimal-scenario" in content
    assert "mode: augmented" in content
    assert "returncode: 0" in content


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_writes_to_default_path_when_none_given(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path, monkeypatch
):
    """Default transcript_dir is reports/raw relative to CWD."""
    monkeypatch.chdir(tmp_path)
    runner = OpenClawRunner()  # no transcript_dir → default
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="x", stderr="y"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.BASELINE)

    expected_root = (tmp_path / "reports" / "raw").resolve()
    assert expected_root.exists()
    assert result.transcript_path is not None
    assert Path(result.transcript_path).resolve().is_relative_to(expected_root)


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_skips_persistence_when_transcript_dir_explicitly_none(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path, monkeypatch
):
    """Passing transcript_dir=None explicitly disables persistence (for tests etc)."""
    monkeypatch.chdir(tmp_path)  # ensure we don't accidentally write to real cwd
    runner = OpenClawRunner(transcript_dir=None)
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="x", stderr="y"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.transcript_path is None
    # And no reports/raw directory should have been created in cwd.
    assert not (tmp_path / "reports" / "raw").exists()


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_transcript_path_format(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """Path follows reports/raw/<scenario>/<mode>/<timestamp>-<hash>.txt."""
    runner = OpenClawRunner(transcript_dir=tmp_path / "raw")
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="x", stderr="y"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.BASELINE)

    assert result.transcript_path is not None
    p = Path(result.transcript_path)
    assert p.parent == (tmp_path / "raw" / scenario.id / "baseline").resolve()
    assert p.suffix == ".txt"
    import re
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}\.txt$", p.name), (
        f"filename must match YYYYMMDD-HHMMSS-XXXXXXXX.txt, got {p.name!r}"
    )


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_persists_transcript_on_agent_failure(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """Nonzero agent exit must still produce a transcript — that's when audit data matters most."""
    runner = OpenClawRunner(transcript_dir=tmp_path / "raw")
    mock_run.side_effect = [
        MagicMock(returncode=2, stdout="partial stdout", stderr="agent crashed: bad flag"),
        MagicMock(returncode=0),  # check (won't be reached, but harmless)
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.transcript_path is not None
    content = Path(result.transcript_path).read_text()
    assert "agent crashed: bad flag" in content
    assert "partial stdout" in content
    assert "returncode: 2" in content


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_persists_transcript_on_timeout(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """Timeout must still write a transcript with whatever metadata is available."""
    runner = OpenClawRunner(transcript_dir=tmp_path / "raw")
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="openclaw", timeout=30)

    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.transcript_path is not None
    content = Path(result.transcript_path).read_text()
    assert "scenario_id: minimal-scenario" in content
    assert "mode: augmented" in content
    # The error context should be captured somewhere in the transcript.
    assert "timeout" in content.lower()


def test_openclaw_runner_persists_transcript_on_spawn_failure(
    scenario_dir: Path, tmp_path: Path
):
    """Missing binary path must still write a transcript so failures are auditable."""
    runner = OpenClawRunner(
        openclaw_bin="/nonexistent/path/openclaw",
        transcript_dir=tmp_path / "raw",
    )
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.transcript_path is not None
    content = Path(result.transcript_path).read_text()
    assert "scenario_id: minimal-scenario" in content
    assert "failed to spawn" in content.lower()


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_openclaw_runner_transcript_includes_argv_and_meta_sections(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """Transcript file must include the documented section markers and argv."""
    runner = OpenClawRunner(transcript_dir=tmp_path / "raw")
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="OUT", stderr="ERR"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    scenario = Scenario.load(scenario_dir)
    result = runner.run(scenario, mode=Mode.AUGMENTED)

    content = Path(result.transcript_path).read_text()
    assert "=== meta ===" in content
    assert "=== argv ===" in content
    assert "=== stderr ===" in content
    assert "=== stdout ===" in content
    # argv should mention the agent subcommand and --json flag we always pass.
    assert "agent" in content
    assert "--json" in content
    # parse_warnings must appear in the meta block (count is 0 here since
    # the mocked stdout/stderr contain no JSON or `{`).
    assert "parse_warnings:" in content


# --- tool-call extraction tests (session log integration) ---


def _session_log_with_tool_calls(
    session_root: Path,
    agent_id: str,
    session_id: str,
    *,
    tool_names: list[str],
) -> Path:
    """Stage a fake OpenClaw session JSONL containing the given tool calls.

    Mirrors the real layout: <session_root>/<agent_id>/sessions/<sid>.jsonl.
    """
    sessions_dir = session_root / agent_id / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_file = sessions_dir / f"{session_id}.jsonl"
    lines = [
        json.dumps({"type": "session", "id": session_id,
                    "timestamp": "2026-04-19T00:00:00Z"}),
        json.dumps({
            "type": "message",
            "id": "a1",
            "timestamp": "2026-04-19T00:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "id": f"tc{i}", "name": name,
                     "arguments": "..."}
                    for i, name in enumerate(tool_names)
                ],
            },
        }),
    ]
    log_file.write_text("\n".join(lines) + "\n")
    return log_file


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_runner_populates_tool_calls_from_session_log(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """Runner must read the session JSONL and populate RunResult.tool_calls.

    Wires together: (1) parsing sessionId from meta.agentMeta.sessionId in the
    OpenClaw JSON output, (2) resolving the per-session log path under the
    configurable session root, (3) extracting per-tool counts.
    """
    sid = "fake-session-abc"
    session_root = tmp_path / "sessions"
    _session_log_with_tool_calls(
        session_root, agent_id="main", session_id=sid,
        tool_names=["exec", "exec"],
    )
    fake_stdout = json.dumps({"meta": {"agentMeta": {
        "sessionId": sid,
        "usage": {"input": 10, "output": 5, "total": 15},
    }}})
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=fake_stdout, stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    runner = OpenClawRunner(
        agent_id="main",
        openclaw_session_root=session_root,
        transcript_dir=None,  # disable persistence for this test
    )
    result = runner.run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tool_calls == {"exec": 2}, f"got {result.tool_calls}"


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_runner_tool_calls_empty_when_session_log_missing(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """If the session log doesn't exist on disk, tool_calls is empty (not a crash)."""
    sid = "no-log-session"
    fake_stdout = json.dumps({"meta": {"agentMeta": {
        "sessionId": sid,
        "usage": {"input": 1, "output": 1, "total": 2},
    }}})
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=fake_stdout, stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    runner = OpenClawRunner(
        agent_id="main",
        openclaw_session_root=tmp_path / "nonexistent",
        transcript_dir=None,
    )
    result = runner.run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tool_calls == {}, f"missing log must yield empty, got {result.tool_calls}"
    # The rest of the result should still be populated normally.
    assert result.tokens == 2


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_runner_tool_calls_empty_when_no_sessionid_in_output(
    mock_run, scenario_dir: Path, fake_openclaw_on_path, tmp_path: Path
):
    """If OpenClaw output omits sessionId (older versions / parse failure),
    we can't know which file to read — return empty rather than guess."""
    fake_stdout = json.dumps({"meta": {"agentMeta": {
        "usage": {"input": 1, "output": 1, "total": 2},
    }}})
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=fake_stdout, stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    runner = OpenClawRunner(
        agent_id="main",
        openclaw_session_root=tmp_path / "sessions",
        transcript_dir=None,
    )
    result = runner.run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert result.tool_calls == {}


@patch("agent_desktop_evals.runners.openclaw.subprocess.run")
def test_runner_default_tool_calls_empty_when_no_introspection(
    mock_run, scenario_dir: Path, fake_openclaw_on_path
):
    """Backward compat: existing test paths that don't stage a session log
    must still produce a valid RunResult with tool_calls={} (not None / not crash)."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    result = OpenClawRunner(transcript_dir=None).run(
        Scenario.load(scenario_dir), mode=Mode.AUGMENTED
    )
    assert result.tool_calls == {}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell-out")
def test_real_subprocess_with_invalid_utf8_does_not_crash(scenario_dir: Path, tmp_path: Path):
    """Replaces the prior mock-only UnicodeDecodeError test, which couldn't
    actually fire (subprocess.run with text=True, errors='replace' substitutes
    U+FFFD for bad bytes — never raises). This is a real integration test:
    spawn a tiny shell script that emits a bad UTF-8 byte and verify the runner
    produces a RunResult instead of crashing.

    The test_uses_errors_replace_for_subprocess_decoding test below remains the
    structural guarantee that errors='replace' is in fact passed to both calls.
    """
    fake_bin = tmp_path / "fake-openclaw"
    # \xc3 starts a 2-byte UTF-8 sequence; \x28 is a stray byte that breaks the
    # sequence — perfect adversarial input for the decoder.
    fake_bin.write_text("#!/bin/sh\nprintf '\\xc3\\x28'\nexit 0\n")
    fake_bin.chmod(0o755)
    runner = OpenClawRunner(openclaw_bin=str(fake_bin))
    result = runner.run(Scenario.load(scenario_dir), mode=Mode.AUGMENTED)
    assert isinstance(result, RunResult), (
        f"runner must return RunResult instead of crashing on bad UTF-8, got {type(result)!r}"
    )
    # tokens must be 0 (no JSON in our bad-bytes payload), parse must not warn
    # (no `{` in the input ⇒ pure-noise path, not corruption path).
    assert result.tokens == 0
    assert result.parse_warnings == 0
