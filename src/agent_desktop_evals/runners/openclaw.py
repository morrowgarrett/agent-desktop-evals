from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, NonNegativeInt, ValidationError

from agent_desktop_evals.runner_base import Mode, RunResult, now_iso
from agent_desktop_evals.scenario import Scenario

# Sentinel distinguishing "use the default transcript_dir" from "explicitly
# disable persistence (None)". Using a sentinel keeps the public default
# discoverable in the signature while preserving back-compat for callers that
# pass transcript_dir=None to opt out (e.g., tests that don't want disk side
# effects).
_DEFAULT_TRANSCRIPT_DIR = object()


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


def _coerce_text(value: object) -> str:
    """Coerce subprocess output (str | bytes | None) to a str for transcript dump.

    text=True normally yields str, but TimeoutExpired.stdout/.stderr can return
    bytes if the timeout fires before stream decoding completes. Replace bad
    bytes rather than raise — the transcript is best-effort audit data.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _format_timestamp(started_at_iso: str) -> str:
    """Reformat an ISO 8601 timestamp as YYYYMMDD-HHMMSS for path inclusion.

    Tolerates both '+00:00' and 'Z' suffixes. Falls back to a naive parse if
    fromisoformat can't handle the input (e.g., a future ISO variant).
    """
    s = started_at_iso
    # Python <3.11's fromisoformat won't accept the trailing 'Z'; normalize it.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Last-resort: strip subsecond / timezone and try a basic parse.
        dt = datetime.utcnow()
    return dt.strftime("%Y%m%d-%H%M%S")


def _write_transcript(
    *,
    base_dir: Path,
    scenario_id: str,
    runner_name: str,
    mode: Mode,
    agent_id: str,
    started_at_iso: str,
    returncode: int,
    parse_warnings: int,
    argv: list[str],
    stderr: str,
    stdout: str,
) -> str:
    """Write the structured transcript to disk and return the absolute path.

    Layout: <base_dir>/<scenario_id>/<mode>/<YYYYMMDD-HHMMSS>-<8-hex-hash>.txt

    The hash is md5 over the full content (NOT for security — collision
    avoidance + a deduplication signal for identical reruns). Use
    usedforsecurity=False where supported to satisfy stricter linters /
    FIPS-restricted environments.
    """
    ts = _format_timestamp(started_at_iso)
    # Stderr-then-stdout matches the existing parser concatenation order
    # (proc.stdout + proc.stderr) inverted — but the order chosen here
    # ("stderr then stdout") puts the upstream banner before the JSON in the
    # human-readable dump, which matches how operators read real captures.
    # (The parser is independent of this ordering since it scans for `{`.)
    argv_block = "\n".join(shlex.quote(str(a)) for a in argv)
    body_sections = (
        "=== meta ===\n"
        f"scenario_id: {scenario_id}\n"
        f"runner: {runner_name}\n"
        f"mode: {mode.value}\n"
        f"agent_id: {agent_id}\n"
        f"started_at: {started_at_iso}\n"
        f"returncode: {returncode}\n"
        f"parse_warnings: {parse_warnings}\n"
        "=== argv ===\n"
        f"{argv_block}\n"
        "=== stderr ===\n"
        f"{stderr}\n"
        "=== stdout ===\n"
        f"{stdout}\n"
    )
    # Hash the content so identical reruns produce identical filenames (a
    # deduplication signal for the operator). md5 is fine here — not security.
    try:
        digest = hashlib.md5(
            body_sections.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
    except TypeError:
        # Older Python versions don't accept usedforsecurity; fall through.
        digest = hashlib.md5(body_sections.encode("utf-8")).hexdigest()
    short_hash = digest[:8]

    target_dir = (base_dir / scenario_id / mode.value).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{ts}-{short_hash}.txt"
    target.write_text(body_sections, encoding="utf-8")
    return str(target)


class OpenClawRunner:
    name = "openclaw"

    def __init__(
        self,
        openclaw_bin: str = "openclaw",
        agent_id: str = "main",
        transcript_dir: Path | str | None | object = _DEFAULT_TRANSCRIPT_DIR,
    ):
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
        # Sentinel resolution: _DEFAULT_TRANSCRIPT_DIR ⇒ Path("reports/raw")
        # resolved against CWD at run-time (NOT init-time, so monkeypatched
        # cwd in tests behaves as expected). Explicit None disables.
        if transcript_dir is _DEFAULT_TRANSCRIPT_DIR:
            self._transcript_dir: Path | None = Path("reports/raw")
        elif transcript_dir is None:
            self._transcript_dir = None
        else:
            self._transcript_dir = Path(transcript_dir)  # type: ignore[arg-type]

    def run(self, scenario: Scenario, mode: Mode) -> RunResult:
        started = now_iso()
        t0 = time.monotonic()

        # Pre-build the spawn argv even on the missing-binary path, so the
        # transcript can record what we *would* have invoked. This keeps the
        # failure-path transcript informative for post-facto auditing.
        argv = [
            self._bin or self._requested_bin, "agent",
            "--agent", self._agent_id,
            "--local",
            "--message", scenario.prompt,
            "--json",
        ]

        if self._bin is None:
            error = f"failed to spawn {self._requested_bin}: not found on PATH"
            return self._finalize(
                scenario=scenario, mode=mode, started=started,
                wallclock_s=time.monotonic() - t0,
                argv=argv, stderr=error, stdout="",
                returncode=-1, success=False, error=error,
                tokens=0, screenshots=0, parse_warnings=0,
            )

        env = os.environ.copy()
        if mode == Mode.BASELINE:
            # Strip directories containing agent-desktop from PATH
            env["PATH"] = self._strip_agent_desktop(env.get("PATH", ""))

        try:
            proc = subprocess.run(
                argv,
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
        except subprocess.TimeoutExpired as te:
            err_msg = f"timeout after {scenario.timeout_seconds}s"
            # te.stdout / te.stderr may be bytes or None (text=True surfaces
            # str when present, but the timeout path can return bytes). Coerce
            # defensively for the transcript dump.
            partial_stdout = _coerce_text(te.stdout)
            partial_stderr = _coerce_text(te.stderr)
            return self._finalize(
                scenario=scenario, mode=mode, started=started,
                wallclock_s=time.monotonic() - t0,
                argv=argv, stderr=partial_stderr or err_msg, stdout=partial_stdout,
                returncode=-1, success=False, error=err_msg,
                tokens=0, screenshots=0, parse_warnings=0,
            )
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError is defensive belt-and-suspenders: errors='replace'
            # above should prevent it, but if a future code path uses errors='strict'
            # we don't want to crash the runner mid-eval.
            err_msg = f"failed to spawn {self._bin}: {e}"
            return self._finalize(
                scenario=scenario, mode=mode, started=started,
                wallclock_s=time.monotonic() - t0,
                argv=argv, stderr=err_msg, stdout="",
                returncode=-1, success=False, error=err_msg,
                tokens=0, screenshots=0, parse_warnings=0,
            )

        wallclock_s = time.monotonic() - t0
        metrics = _parse_metrics(transcript)

        # Even if check_state would pass, a failed agent invocation must not be
        # reported as success: a leftover desktop state from a prior run could
        # otherwise produce a false-positive success when the agent itself
        # crashed or exited with an error.
        if proc.returncode != 0:
            stderr_msg = proc.stderr.strip()[:500] if proc.stderr else "<no stderr>"
            return self._finalize(
                scenario=scenario, mode=mode, started=started,
                wallclock_s=wallclock_s, argv=argv,
                stderr=proc.stderr or "", stdout=proc.stdout or "",
                returncode=proc.returncode, success=False,
                error=f"agent exited {proc.returncode}: {stderr_msg}",
                tokens=metrics["tokens"], screenshots=metrics["screenshots"],
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
            return self._finalize(
                scenario=scenario, mode=mode, started=started,
                wallclock_s=wallclock_s, argv=argv,
                stderr=proc.stderr or "", stdout=proc.stdout or "",
                returncode=proc.returncode, success=False,
                error=f"check_state timed out after {scenario.check_timeout_seconds}s",
                tokens=metrics["tokens"], screenshots=metrics["screenshots"],
                parse_warnings=metrics["parse_warnings"],
            )
        success = check.returncode == scenario.expect_exit_code

        return self._finalize(
            scenario=scenario, mode=mode, started=started,
            wallclock_s=wallclock_s, argv=argv,
            stderr=proc.stderr or "", stdout=proc.stdout or "",
            returncode=proc.returncode, success=success, error=error,
            tokens=metrics["tokens"], screenshots=metrics["screenshots"],
            parse_warnings=metrics["parse_warnings"],
        )

    def _finalize(
        self,
        *,
        scenario: Scenario,
        mode: Mode,
        started: str,
        wallclock_s: float,
        argv: list[str],
        stderr: str,
        stdout: str,
        returncode: int,
        success: bool,
        error: str | None,
        tokens: int,
        screenshots: int,
        parse_warnings: int,
    ) -> RunResult:
        """Build a RunResult, persisting the transcript first when enabled.

        Centralizing the return path here ensures every code path (success,
        agent-failure, timeout, spawn-error, check-timeout) gets a transcript
        written when persistence is enabled. Transcripts on failure paths are
        the most valuable data for post-facto investigation.
        """
        transcript_path: str | None = None
        if self._transcript_dir is not None:
            try:
                transcript_path = _write_transcript(
                    base_dir=self._transcript_dir,
                    scenario_id=scenario.id,
                    runner_name=self.name,
                    mode=mode,
                    agent_id=self._agent_id,
                    started_at_iso=started,
                    returncode=returncode,
                    parse_warnings=parse_warnings,
                    argv=argv,
                    stderr=stderr,
                    stdout=stdout,
                )
            except OSError:
                # Persistence must never mask a real result. If disk-write
                # fails (full FS, permissions), fall back to None so the
                # caller still receives the metrics; the operator can retry.
                transcript_path = None

        return RunResult(
            scenario_id=scenario.id,
            runner_name=self.name,
            mode=mode,
            success=success,
            tokens=tokens,
            screenshots=screenshots,
            wallclock_s=wallclock_s,
            started_at_iso=started,
            error=error,
            parse_warnings=parse_warnings,
            transcript_path=transcript_path,
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
