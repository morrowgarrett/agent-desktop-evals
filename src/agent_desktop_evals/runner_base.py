from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel

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


class Runner(Protocol):
    name: str

    def run(self, scenario: Scenario, mode: Mode) -> RunResult: ...


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
