from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, NonNegativeInt, ValidationError

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario


class _TurnComplete(BaseModel):
    """Strict shape for a turn_complete event.

    Tokens must be non-negative ints; null degrades to zero (handled by
    field-level coercion before validation). Strings, floats, negatives reject.
    """

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0


def _coerce_null(value: Any) -> Any:
    """Treat JSON null as 0 for token fields (pre-validation tolerance)."""
    return 0 if value is None else value


def _split_objects(blob: str) -> list[str]:
    """Split a string that may contain one or more JSON objects (compact or pretty-printed)
    into individual top-level JSON object strings using a brace-balance scan.

    Skips characters outside top-level objects (so log preamble/trailing text is ignored).
    Strings and escapes are tracked so braces inside them don't confuse the scanner.
    """
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(blob):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(blob[start : i + 1])
                    start = -1
    return out


def _parse_metrics(transcript: str) -> dict[str, int]:
    """Sum token counts and count screenshot tool calls from a transcript.

    Accepts both compact JSONL and pretty-printed multi-line JSON. Validates
    event shapes via Pydantic; rejected events increment parse_warnings rather
    than silently miscounting.
    """
    tokens = 0
    screenshots = 0
    parse_warnings = 0

    for raw in _split_objects(transcript):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            parse_warnings += 1
            continue
        if not isinstance(event, dict):
            parse_warnings += 1
            continue

        kind = event.get("event")
        if kind == "turn_complete":
            payload = {
                "input_tokens": _coerce_null(event.get("input_tokens", 0)),
                "output_tokens": _coerce_null(event.get("output_tokens", 0)),
            }
            try:
                tc = _TurnComplete.model_validate(payload, strict=True)
            except ValidationError:
                parse_warnings += 1
                continue
            tokens += tc.input_tokens + tc.output_tokens
        elif kind == "tool_call" and event.get("tool") == "screenshot":
            screenshots += 1

    return {"tokens": tokens, "screenshots": screenshots, "parse_warnings": parse_warnings}


class OpenClawRunner:
    name = "openclaw"

    def __init__(self, openclaw_bin: str = "openclaw"):
        # Resolve to an absolute path at init time. This way, BASELINE-mode
        # PATH-stripping (which removes dirs containing agent-desktop) doesn't
        # also break openclaw when both binaries live in the same directory.
        # The resolved spawn path is invariant; PATH only affects what the
        # *agent* subprocess can subsequently look up.
        if os.path.isabs(openclaw_bin):
            self._bin = openclaw_bin if Path(openclaw_bin).exists() else None
        else:
            self._bin = shutil.which(openclaw_bin)
        self._requested_bin = openclaw_bin

    def run(self, scenario: Scenario, mode: Mode) -> RunResult:
        started = now_iso()
        t0 = time.monotonic()

        if self._bin is None:
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False, tokens=0, screenshots=0,
                wallclock_s=time.monotonic() - t0, started_at_iso=started,
                error=f"failed to spawn {self._requested_bin}: not found on PATH",
            )

        env = os.environ.copy()
        if mode == Mode.BASELINE:
            # Strip directories containing agent-desktop from PATH
            env["PATH"] = self._strip_agent_desktop(env.get("PATH", ""))

        try:
            proc = subprocess.run(
                [self._bin, "agent", "--local", "--message", scenario.prompt, "--json"],
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
        except (FileNotFoundError, PermissionError, OSError) as e:
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False, tokens=0, screenshots=0,
                wallclock_s=time.monotonic() - t0, started_at_iso=started,
                error=f"failed to spawn {self._bin}: {e}",
            )

        wallclock_s = time.monotonic() - t0
        metrics = _parse_metrics(transcript)

        # Even if check_state would pass, a failed agent invocation must not be
        # reported as success: a leftover desktop state from a prior run could
        # otherwise produce a false-positive success when the agent itself
        # crashed or exited with an error.
        if proc.returncode != 0:
            stderr_msg = proc.stderr.strip()[:500] if proc.stderr else "<no stderr>"
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False,
                tokens=metrics["tokens"], screenshots=metrics["screenshots"],
                wallclock_s=wallclock_s, started_at_iso=started,
                error=f"agent exited {proc.returncode}: {stderr_msg}",
                parse_warnings=metrics["parse_warnings"],
            )

        # Verify success via the scenario's check script.
        # check_state inherits the parent env unmodified — even in BASELINE mode,
        # the *check* must have full PATH (gsettings, dconf, etc.).
        try:
            check = subprocess.run(
                ["bash", str(scenario.check_script)],
                capture_output=True, text=True,
                timeout=scenario.check_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False,
                tokens=metrics["tokens"], screenshots=metrics["screenshots"],
                wallclock_s=wallclock_s, started_at_iso=started,
                error=f"check_state timed out after {scenario.check_timeout_seconds}s",
                parse_warnings=metrics["parse_warnings"],
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
            parse_warnings=metrics["parse_warnings"],
        )

    @staticmethod
    def _strip_agent_desktop(path: str) -> str:
        """Remove any PATH entry that contains an executable agent-desktop binary."""
        kept: list[str] = []
        for d in path.split(os.pathsep):
            if not d:
                continue
            candidate = Path(d, "agent-desktop")
            if candidate.exists() and os.access(candidate, os.X_OK):
                continue
            kept.append(d)
        return os.pathsep.join(kept)
