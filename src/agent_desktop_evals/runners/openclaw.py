from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, NonNegativeInt, ValidationError

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario


class _Usage(BaseModel):
    """Strict shape for the meta.agentMeta.usage block emitted by OpenClaw 2026.4.9.

    Tokens must be non-negative ints; strings, floats, negatives reject so that
    upstream-format drift surfaces as a parse_warning rather than silently
    miscounting.
    """

    input: NonNegativeInt
    output: NonNegativeInt


def _parse_metrics(transcript: str) -> dict[str, int]:
    """Extract token counts and (eventually) screenshot counts from real OpenClaw output.

    Real shape, captured 2026-04-18 from `openclaw agent --agent main --local
    --message ... --json` (see tests/fixtures/openclaw-smoke-output.txt):

        [plugins] plugins.allow is empty; ...   <-- non-JSON banner on first line
        {
          "payloads": [...],
          "meta": {
            "agentMeta": {
              "usage": { "input": 114, "output": 5, ... }
            }
          }
        }

    Strategy: locate the first `{` (skipping the banner), then json.loads from
    there. The output is a single object, not JSONL.

    TODO(screenshots): real tool-usage shape is unknown — the smoke fixture
    only contains tool *definitions* under meta.agentMeta.systemPromptReport.
    Once a tool-using run is captured, refine screenshot detection here.
    """
    tokens = 0
    screenshots = 0  # TODO: refine when we have tool-usage output to inspect
    parse_warnings = 0

    brace_idx = transcript.find("{")
    if brace_idx == -1:
        # No JSON content at all — empty input or banner-only. Not a warning.
        return {"tokens": 0, "screenshots": 0, "parse_warnings": 0}

    try:
        data = json.loads(transcript[brace_idx:])
    except json.JSONDecodeError:
        return {"tokens": 0, "screenshots": 0, "parse_warnings": 1}

    if not isinstance(data, dict):
        return {"tokens": 0, "screenshots": 0, "parse_warnings": 1}

    usage_raw = (
        data.get("meta", {}).get("agentMeta", {}).get("usage")
        if isinstance(data.get("meta"), dict)
        and isinstance(data["meta"].get("agentMeta"), dict)
        else None
    )
    if not isinstance(usage_raw, dict):
        parse_warnings += 1
    else:
        try:
            usage = _Usage.model_validate(usage_raw, strict=True)
        except ValidationError:
            parse_warnings += 1
        else:
            tokens = usage.input + usage.output

    return {"tokens": tokens, "screenshots": screenshots, "parse_warnings": parse_warnings}


class OpenClawRunner:
    name = "openclaw"

    def __init__(self, openclaw_bin: str = "openclaw", agent_id: str = "main"):
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
        # OpenClaw 2026.4.9 requires --agent / --to / --session-id; "main" is
        # the user's default agent id and matches typical local installs.
        self._agent_id = agent_id

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
                [
                    self._bin, "agent",
                    "--agent", self._agent_id,
                    "--local",
                    "--message", scenario.prompt,
                    "--json",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=scenario.timeout_seconds,
                check=False,
            )
            # OpenClaw 2026.4.9 writes its JSON to STDERR (after a banner line).
            # Concatenate both streams so a future shift to stdout doesn't regress
            # token accounting.
            transcript = (proc.stderr or "") + (proc.stdout or "")
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
        """Remove PATH entries that resolve agent-desktop to an executable.

        Preserves empty segments verbatim: on POSIX, an empty PATH entry
        (leading/trailing/double colon) means 'current directory' and dropping
        it changes command-resolution semantics rather than just removing
        agent-desktop. Uses shutil.which for the executable check so this
        agrees with env.has_agent_desktop_on_path (a directory named
        'agent-desktop' is not an executable and must not trigger a strip).
        """
        kept: list[str] = []
        for d in path.split(os.pathsep):
            # shutil.which("agent-desktop", path="") is undefined; the empty
            # segment carries POSIX 'current directory' meaning and we keep it
            # verbatim without performing the executable check.
            if d and shutil.which("agent-desktop", path=d) is not None:
                continue
            kept.append(d)
        return os.pathsep.join(kept)
