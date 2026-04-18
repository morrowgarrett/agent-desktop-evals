from __future__ import annotations

import os
import shutil


def detect_display_server() -> str:
    """Return 'x11', 'wayland', or 'unknown' based on $XDG_SESSION_TYPE."""
    val = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if val in {"x11", "wayland"}:
        return val
    return "unknown"


def has_agent_desktop_on_path() -> bool:
    """True iff `agent-desktop` is found on the current PATH."""
    return shutil.which("agent-desktop") is not None
