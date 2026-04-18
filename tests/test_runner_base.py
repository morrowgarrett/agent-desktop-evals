from pathlib import Path

from agent_desktop_bench.runner_base import Mode, RunResult
from agent_desktop_bench.runners.stub import StubRunner
from agent_desktop_bench.scenario import Scenario


def test_stub_runner_returns_canned_success(scenario_dir: Path):
    scenario = Scenario.load(scenario_dir)
    runner = StubRunner(success=True, tokens=123, screenshots=4, wallclock_s=5.5)

    result = runner.run(scenario, mode=Mode.BASELINE)

    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.tokens == 123
    assert result.screenshots == 4
    assert result.wallclock_s == 5.5
    assert result.mode == Mode.BASELINE
    assert result.scenario_id == scenario.id
    assert result.runner_name == "stub"


def test_stub_runner_returns_canned_failure(scenario_dir: Path):
    scenario = Scenario.load(scenario_dir)
    runner = StubRunner(success=False, tokens=0, screenshots=0, wallclock_s=0.1)

    result = runner.run(scenario, mode=Mode.AUGMENTED)

    assert result.success is False
    assert result.mode == Mode.AUGMENTED


def test_run_result_has_iso_timestamp(scenario_dir: Path):
    scenario = Scenario.load(scenario_dir)
    runner = StubRunner(success=True, tokens=1, screenshots=0, wallclock_s=0.1)
    result = runner.run(scenario, mode=Mode.BASELINE)
    assert result.started_at_iso.endswith("+00:00") or result.started_at_iso.endswith("Z")
