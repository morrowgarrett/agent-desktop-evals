from agent_desktop_bench.env import detect_display_server, has_agent_desktop_on_path


def test_detect_display_server_prefers_xdg_session_type(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert detect_display_server() == "x11"


def test_detect_display_server_handles_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert detect_display_server() == "wayland"


def test_detect_display_server_unknown_when_unset(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert detect_display_server() == "unknown"


def test_has_agent_desktop_on_path_when_present(monkeypatch, tmp_path):
    fake_bin = tmp_path / "agent-desktop"
    fake_bin.write_text("#!/bin/sh\necho fake\n")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert has_agent_desktop_on_path() is True


def test_has_agent_desktop_on_path_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert has_agent_desktop_on_path() is False
