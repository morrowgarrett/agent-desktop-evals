"""Read OpenClaw's per-session JSONL log to count tool invocations.

OpenClaw appends one JSON-per-line event to
``~/.openclaw/agents/<agent_id>/sessions/<sessionId>.jsonl`` as the agent runs.

Critical: the file is append-only across reuses of the same agent. Empirically,
OpenClaw writes a single ``{"type": "session", "id": <sid>, ...}`` event when
the agent is first created and never writes another. Every subsequent run
against that agent appends new ``message`` / ``custom`` / ... events to the
SAME file with no fresh anchor. A naive "session-event-as-anchor" approach
therefore scoops up every tool call ever made against that agent — verified
locally producing exec:183 for a single-run scenario where ground truth was
exec:4. So we cannot use the session-creation event as the per-run anchor.

What we CAN anchor on is the user-prompt event for THIS run. The runner sent
the agent ``scenario.prompt`` verbatim via ``--message``; OpenClaw records
that as a ``message`` event with ``role: user`` whose ``content`` blocks'
joined text equals our prompt. We find the LAST such event in the file and
count toolCall blocks strictly after it.

Event shape (top-level): ``{"type": "...", "id": "...", "timestamp": "..."}``.
Observed top-level types: ``message``, ``custom``, ``compaction``,
``thinking_level_change``, ``session``, ``model_change``.

Tool invocations live inside ``message`` events with ``message.role ==
"assistant"``. The assistant's ``content`` array contains blocks; blocks of
``type == "toolCall"`` carry ``{id, name, arguments, ...}``. We count those.

``toolResult`` events represent the *result* of a prior call (carry a
``toolName`` field) and are NOT counted as invocations.

Sessionid is still passed in (and validated against the file's session
anchor when present) for forward-compatibility: if a future OpenClaw release
ever starts writing a fresh session event per run, that becomes a stronger
anchor than prompt match.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def _joined_text(content: object) -> str:
    """Join the text of all 'text'-type blocks in a message content array.

    Returns the empty string if content isn't a list or has no text blocks.
    """
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def extract_tool_calls(
    session_log_path: Path,
    session_id: str,
    prompt: str | None = None,
) -> dict[str, int]:
    """Count toolCall events by tool name within the given run.

    The anchor for "this run's events" is the LAST user-message event whose
    joined text equals ``prompt``. Pass ``prompt`` to anchor reliably; without
    it we fall back to the session-creation event, which (per module docstring)
    is rarely the right boundary because OpenClaw never writes a new one for
    subsequent runs against the same agent.

    Returns a dict mapping tool name -> invocation count, e.g.
    ``{"exec": 4, "read": 2}``. Returns an empty dict when:

    - the session log file does not exist (logs a warning),
    - the file is empty,
    - no usable anchor (matching prompt or session event) is found,
    - no toolCall blocks appear after the anchor.

    Lines that fail JSON decoding are skipped (logged at debug level) so a
    single corrupt line doesn't void the whole run's tool-call telemetry.
    """
    if not session_log_path.exists():
        logger.warning(
            "openclaw session log not found at %s (session_id=%s); "
            "tool_calls will be empty",
            session_log_path,
            session_id,
        )
        return {}

    # Pass 1: collect all events as parsed dicts (skip undecodable lines).
    events: list[dict] = []
    try:
        with session_log_path.open(encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError as e:
                    logger.debug(
                        "skipping malformed JSON at %s:%d (%s)",
                        session_log_path, lineno, e,
                    )
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError as e:
        logger.warning(
            "failed to read openclaw session log %s: %s; tool_calls will be empty",
            session_log_path, e,
        )
        return {}

    # Pass 2: locate the anchor for THIS run. Prefer the LAST user-prompt
    # event matching ``prompt`` exactly — that's the start of this run's turn
    # and it works regardless of how many prior runs are appended above.
    anchor_index: int | None = None
    if prompt is not None:
        for i, ev in enumerate(events):
            if ev.get("type") != "message":
                continue
            message = ev.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            if _joined_text(message.get("content")) == prompt:
                anchor_index = i  # keep updating ⇒ LAST matching
    # Fallback: try the session-creation event. Useful only when the prompt
    # match is unavailable (caller didn't pass it, or test fixtures predate
    # the prompt-anchor convention).
    if anchor_index is None:
        for i, ev in enumerate(events):
            if ev.get("type") == "session" and ev.get("id") == session_id:
                anchor_index = i
    if anchor_index is None:
        logger.warning(
            "no anchor (prompt or session id=%s) found in %s; tool_calls "
            "will be empty (refusing to attribute historical events to this run)",
            session_id, session_log_path,
        )
        return {}

    # Pass 3: count toolCall blocks in events strictly AFTER the anchor.
    counts: Counter[str] = Counter()
    for ev in events[anchor_index + 1:]:
        if ev.get("type") != "message":
            continue
        message = ev.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "toolCall":
                continue
            name = block.get("name")
            if isinstance(name, str) and name:
                counts[name] += 1
    return dict(counts)
