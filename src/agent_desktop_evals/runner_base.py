from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from agent_desktop_evals.scenario import Scenario


class Mode(str, Enum):  # noqa: UP042  # spec uses (str, Enum); equivalent to StrEnum on 3.12
    BASELINE = "baseline"
    AUGMENTED = "augmented"


class RunResult(BaseModel):
    scenario_id: str
    runner_name: str
    mode: Mode
    success: bool
    tokens: int
    screenshots: int
    wallclock_s: float
    steps: int = 0
    transcript_path: str | None = None
    started_at_iso: str
    error: str | None = None
    parse_warnings: int = 0
    # Per-tool-call counts keyed by tool name. Populated by runners that can
    # introspect their agent's tool-invocation log (e.g. OpenClawRunner reading
    # the per-session JSONL after the run). Non-introspecting runners leave
    # this empty.
    tool_calls: dict[str, int] = Field(default_factory=dict)
    # The session id used by the agent for this run, when known. OpenClawRunner
    # generates a fresh uuid4 per run() and passes it as `--session-id` to
    # prevent context contamination across runs (without this, the persistent
    # `--agent <id>` session accumulates context across all invocations).
    # Non-OpenClaw runners leave this None.
    session_id: str | None = None


class Runner(Protocol):
    name: str

    def run(self, scenario: Scenario, mode: Mode) -> RunResult: ...


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
