import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def scenario_dir(tmp_path: Path) -> Path:
    """A minimal valid scenario directory."""
    d = tmp_path / "minimal-scenario"
    d.mkdir()
    (d / "scenario.toml").write_text(textwrap.dedent("""
        id = "minimal-scenario"
        title = "Minimal scenario for tests"
        target_app = "gnome-calculator"
        timeout_seconds = 30

        [check]
        script = "check_state.sh"
        expect_exit_code = 0
    """).strip())
    (d / "prompt.md").write_text("Press the 5 button.")
    (d / "check_state.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (d / "check_state.sh").chmod(0o755)
    (d / "README.md").write_text("# Minimal scenario\n")
    return d
