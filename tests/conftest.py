import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def fake_openclaw_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage a fake openclaw executable on PATH so OpenClawRunner.__init__ can resolve it.

    Tests that mock subprocess.run still need shutil.which to find a binary at init.
    """
    import os as _os
    bindir = tmp_path / "_fake_bin"
    bindir.mkdir()
    oc = bindir / "openclaw"
    oc.write_text("#!/bin/bash\nexit 0\n")
    oc.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{_os.pathsep}{_os.environ.get('PATH', '')}")
    return oc


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
