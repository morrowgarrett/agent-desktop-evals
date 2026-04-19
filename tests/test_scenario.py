from pathlib import Path

import pytest

from agent_desktop_evals.scenario import Scenario, ScenarioError


def test_load_minimal_scenario(scenario_dir: Path):
    s = Scenario.load(scenario_dir)
    assert s.id == "minimal-scenario"
    assert s.title == "Minimal scenario for tests"
    assert s.target_app == "gnome-calculator"
    assert s.timeout_seconds == 30
    assert s.prompt == "Press the 5 button."
    assert s.check_script == scenario_dir / "check_state.sh"
    assert s.expect_exit_code == 0


def test_missing_scenario_toml_raises(tmp_path: Path):
    with pytest.raises(ScenarioError, match=r"scenario\.toml"):
        Scenario.load(tmp_path)


def test_missing_prompt_raises(scenario_dir: Path):
    (scenario_dir / "prompt.md").unlink()
    with pytest.raises(ScenarioError, match=r"prompt\.md"):
        Scenario.load(scenario_dir)


def test_missing_check_script_raises(scenario_dir: Path):
    (scenario_dir / "check_state.sh").unlink()
    with pytest.raises(ScenarioError, match="check script"):
        Scenario.load(scenario_dir)


def test_id_must_match_directory_name(scenario_dir: Path):
    (scenario_dir / "scenario.toml").write_text(
        'id = "wrong-id"\n'
        'title = "X"\n'
        'target_app = "x"\n'
        'timeout_seconds = 1\n'
        '[check]\nscript = "check_state.sh"\nexpect_exit_code = 0\n'
    )
    with pytest.raises(ScenarioError, match=r"id .* must match"):
        Scenario.load(scenario_dir)


def test_check_script_rejects_absolute_path(scenario_dir: Path):
    """check.script must be a relative path within the scenario directory."""
    (scenario_dir / "scenario.toml").write_text(
        'id = "minimal-scenario"\n'
        'title = "X"\n'
        'target_app = "x"\n'
        'timeout_seconds = 1\n'
        '[check]\nscript = "/etc/passwd"\nexpect_exit_code = 0\n'
    )
    with pytest.raises(ScenarioError, match=r"check\.script"):
        Scenario.load(scenario_dir)


def test_check_script_rejects_parent_traversal(scenario_dir: Path):
    """check.script with '..' segments must be rejected."""
    (scenario_dir / "scenario.toml").write_text(
        'id = "minimal-scenario"\n'
        'title = "X"\n'
        'target_app = "x"\n'
        'timeout_seconds = 1\n'
        '[check]\nscript = "../foo.sh"\nexpect_exit_code = 0\n'
    )
    with pytest.raises(ScenarioError, match=r"check\.script"):
        Scenario.load(scenario_dir)
