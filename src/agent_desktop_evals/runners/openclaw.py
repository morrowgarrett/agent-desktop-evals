from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario


def _parse_metrics(transcript: str) -> dict[str, int]:
    """Sum token counts and count screenshot tool calls from a JSONL transcript.

    Lines that aren't valid JSON are skipped — OpenClaw mixes structured events
    with human log lines, and we only want the structured ones.
    """
    tokens = 0
    screenshots = 0
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "turn_complete":
            tokens += int(event.get("input_tokens") or 0) + int(event.get("output_tokens") or 0)
        if event.get("event") == "tool_call" and event.get("tool") == "screenshot":
            screenshots += 1
    return {"tokens": tokens, "screenshots": screenshots}


class OpenClawRunner:
    name = "openclaw"

    def __init__(self, openclaw_bin: str = "openclaw"):
        self._bin = openclaw_bin

    def run(self, scenario: Scenario, mode: Mode) -> RunResult:
        env = os.environ.copy()
        if mode == Mode.BASELINE:
            # Strip directories containing agent-desktop from PATH
            env["PATH"] = self._strip_agent_desktop(env.get("PATH", ""))

        started = now_iso()
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [self._bin, "chat", "--print", "--json", scenario.prompt],
                env=env,
                capture_output=True,
                text=True,
                timeout=scenario.timeout_seconds,
                check=False,
            )
            transcript = proc.stdout
            error = proc.stderr if proc.returncode != 0 else None
        except subprocess.TimeoutExpired:
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False, tokens=0, screenshots=0,
                wallclock_s=time.monotonic() - t0, started_at_iso=started,
                error=f"timeout after {scenario.timeout_seconds}s",
            )

        wallclock_s = time.monotonic() - t0
        metrics = _parse_metrics(transcript)

        # Verify success via the scenario's check script.
        # check_state inherits the parent env unmodified — even in BASELINE mode,
        # the *check* must have full PATH (gsettings, dconf, etc.).
        check = subprocess.run(
            ["bash", str(scenario.check_script)],
            capture_output=True, text=True,
        )
        success = check.returncode == scenario.expect_exit_code

        return RunResult(
            scenario_id=scenario.id,
            runner_name=self.name,
            mode=mode,
            success=success,
            tokens=metrics["tokens"],
            screenshots=metrics["screenshots"],
            wallclock_s=wallclock_s,
            started_at_iso=started,
            error=error,
        )

    @staticmethod
    def _strip_agent_desktop(path: str) -> str:
        """Remove any PATH entry that contains an agent-desktop binary."""
        kept: list[str] = []
        for d in path.split(os.pathsep):
            if not d:
                continue
            if Path(d, "agent-desktop").exists():
                continue
            kept.append(d)
        return os.pathsep.join(kept)
