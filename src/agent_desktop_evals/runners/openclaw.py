from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, NonNegativeInt, ValidationError

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario


class _Usage(BaseModel):
    """Shape for the meta.agentMeta.usage block emitted by OpenClaw 2026.4.9.

    Tokens must be non-negative ints; strings, floats, negatives reject so that
    upstream-format drift on KNOWN fields surfaces as a parse_warning rather
    than silently miscounting.

    extra='allow' (NOT 'forbid'): the day OpenClaw adds e.g. reasoning_tokens
    for o-series / gpt-5 reasoning models, we'd otherwise hard-zero every run
    with a parse_warning. Allow unknown keys, surface them via model_extra in
    _parse_metrics as a drift signal, but keep the known counts flowing.
    """

    model_config = ConfigDict(extra="allow")

    # Field names mirror the upstream OpenClaw JSON keys verbatim. Aliasing
    # would let typos pass; keeping mixedCase verbatim is intentional.
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cacheRead: NonNegativeInt = 0  # noqa: N815
    cacheWrite: NonNegativeInt = 0  # noqa: N815
    total: NonNegativeInt | None = None


def _tokens_from_usage(usage: _Usage) -> int:
    """Use the precomputed total when truthy; otherwise sum all four fields.

    OpenClaw's createUsageAccumulator precomputes total = input+output+cacheRead+cacheWrite;
    we prefer that over re-summing to avoid drift if the upstream definition changes.
    Truthy (not 'is not None') so a buggy upstream emitting total=0 with nonzero
    components self-heals to the component sum rather than reporting zero.
    """
    if usage.total:
        return usage.total
    return usage.input + usage.output + usage.cacheRead + usage.cacheWrite


# Bound on raw_decode attempts that fail before we declare the input
# pathologically corrupt. Real OpenClaw outputs have a single-digit number of
# braces in banners; 10K is generous insurance against an adversarial worst
# case (e.g., stderr spam of stray `{`) without bailing on real noise.
_MAX_FAILED_DECODE_ATTEMPTS = 10_000


def _find_json_objects(text: str) -> tuple[list[dict], int]:
    """Find all top-level JSON objects in a text stream, ignoring non-JSON content.

    Uses json.JSONDecoder.raw_decode to scan past banners, log lines, and other
    noise; advances past each successfully decoded object and continues scanning.
    Skips '{' that don't begin valid JSON (e.g., banners containing braces).

    Returns (objects, failed_decode_attempts). The caller uses the failed count
    to distinguish 'no `{` in input at all' (truly noise — OK) from 'found `{`
    but every raw_decode attempt failed' (corruption — must warn).

    Bounded at _MAX_FAILED_DECODE_ATTEMPTS to prevent O(N^2) hangs on
    adversarial inputs like 100K stray braces.
    """
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    failed = 0
    i = 0
    while i < len(text) and failed < _MAX_FAILED_DECODE_ATTEMPTS:
        brace = text.find("{", i)
        if brace == -1:
            break
        try:
            obj, end_offset = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            failed += 1
            i = brace + 1  # this '{' didn't start valid JSON; advance and retry
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        i = brace + end_offset
    return objects, failed


def _parse_metrics(transcript: str) -> dict[str, int]:
    """Extract token counts and (eventually) screenshot counts from real OpenClaw output.

    Real shape, captured 2026-04-18 from `openclaw agent --agent main --local
    --message ... --json` (see tests/fixtures/openclaw-smoke-output.txt):

        [plugins] plugins.allow is empty; ...   <-- non-JSON banner on stderr
        {                                       <-- JSON on STDOUT
          "payloads": [...],
          "meta": {
            "agentMeta": {
              "usage": { "input": 114, "output": 5, "cacheRead": 148608,
                         "cacheWrite": 0, "total": 148727 }
            }
          }
        }

    Verified against OpenClaw source at register.agent-DSGqePoo.js:95 →
    writeRuntimeJson → runtime-BXaxArxm.js:21 writeStdout (process.stdout.write).
    The smoke fixture is a 2>&1 capture interleaving streams.

    Cumulative semantics: per pi-embedded-Vw-lS5ti.js:32847 mergeUsageIntoAccumulator,
    every emission of agentMeta carries the running cumulative usageAccumulator
    (target.input += usage.input). Across multiple JSON objects we take the LAST
    cumulative total — summing would N-count tokens for any future stream,
    retry, or interim error-path agentMeta emission.

    TODO(screenshots): real tool-usage shape is unknown — the smoke fixture
    only contains tool *definitions* under meta.agentMeta.systemPromptReport.
    Once a tool-using run is captured, refine screenshot detection here.
    """
    tokens = 0
    screenshots = 0  # TODO: refine when we have tool-usage output to inspect
    parse_warnings = 0

    objects, failed_decodes = _find_json_objects(transcript)
    if not objects:
        # Distinguish two cases:
        #   - failed_decodes == 0: no `{` in input ⇒ pure noise/empty (silent OK).
        #   - failed_decodes  > 0: `{` present but every raw_decode failed ⇒
        #     corruption (e.g., U+FFFD landed in a JSON value via errors='replace',
        #     or pathological input hit the bound). Operator must get a signal.
        warnings = 1 if failed_decodes > 0 else 0
        return {"tokens": 0, "screenshots": 0, "parse_warnings": warnings}

    last_total = None
    for data in objects:
        meta = data.get("meta")
        if not isinstance(meta, dict):
            parse_warnings += 1
            continue
        agent_meta = meta.get("agentMeta")
        if not isinstance(agent_meta, dict):
            parse_warnings += 1
            continue
        usage_raw = agent_meta.get("usage")
        if not isinstance(usage_raw, dict):
            parse_warnings += 1
            continue
        try:
            usage = _Usage.model_validate(usage_raw, strict=True)
        except ValidationError:
            parse_warnings += 1
            continue
        # Schema drift: unknown keys pass extra='allow' but must surface as a
        # warning so we notice and update the model deliberately.
        if usage.model_extra:
            parse_warnings += 1
        last_total = _tokens_from_usage(usage)

    tokens = last_total or 0
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
                errors="replace",  # tolerate non-UTF8 bytes in subprocess output
                timeout=scenario.timeout_seconds,
                check=False,
            )
            # OpenClaw 2026.4.9 writes its JSON to STDOUT (verified against
            # register.agent-DSGqePoo.js:95 → writeRuntimeJson →
            # runtime-BXaxArxm.js:21 writeStdout → process.stdout.write). The
            # colored '[plugins]' banner from console.error goes to stderr.
            # Concatenate both streams so the parser still recovers tokens if
            # OpenClaw ever shifts which stream it emits on.
            transcript = (proc.stdout or "") + (proc.stderr or "")
            error = proc.stderr if proc.returncode != 0 else None
        except subprocess.TimeoutExpired:
            return RunResult(
                scenario_id=scenario.id, runner_name=self.name, mode=mode,
                success=False, tokens=0, screenshots=0,
                wallclock_s=time.monotonic() - t0, started_at_iso=started,
                error=f"timeout after {scenario.timeout_seconds}s",
            )
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError is defensive belt-and-suspenders: errors='replace'
            # above should prevent it, but if a future code path uses errors='strict'
            # we don't want to crash the runner mid-eval.
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
                errors="replace",  # tolerate non-UTF8 bytes in check_state output
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
