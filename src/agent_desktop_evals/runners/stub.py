from __future__ import annotations

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario


class StubRunner:
    name = "stub"

    def __init__(self, *, success: bool, tokens: int, screenshots: int, wallclock_s: float):
        self._success = success
        self._tokens = tokens
        self._screenshots = screenshots
        self._wallclock_s = wallclock_s

    def run(self, scenario: Scenario, mode: Mode) -> RunResult:
        return RunResult(
            scenario_id=scenario.id,
            runner_name=self.name,
            mode=mode,
            success=self._success,
            tokens=self._tokens,
            screenshots=self._screenshots,
            wallclock_s=self._wallclock_s,
            started_at_iso=now_iso(),
        )
