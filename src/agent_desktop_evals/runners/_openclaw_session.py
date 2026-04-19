"""Read OpenClaw's per-session JSONL log to count tool invocations.

OpenClaw appends one JSON-per-line event to
``~/.openclaw/agents/<agent_id>/sessions/<sessionId>.jsonl`` as the agent runs.
The file is append-only across reuses of the same agent, so a single file may
contain events from multiple distinct logical sessions stitched together.

Event shape (top-level): ``{"type": "...", "id": "...", "timestamp": "..."}``.
Observed top-level types: ``message``, ``custom``, ``compaction``,
``thinking_level_change``, ``session``, ``model_change``.

Tool invocations live inside ``message`` events with ``message.role ==
"assistant"``. The assistant's ``content`` array contains blocks; blocks of
``type == "toolCall"`` carry ``{id, name, arguments, ...}``. We count those.

``toolResult`` events represent the *result* of a prior call (carry a
``toolName`` field) and are NOT counted as invocations.

Session anchoring: the JSON output of an OpenClaw run exposes
``meta.agentMeta.sessionId``. We find the matching ``session`` event in the log
(``event["type"] == "session"`` and ``event["id"] == session_id``) and only
count toolCall blocks in events APPENDED AFTER that anchor, so events written
during prior runs against the same agent file don't get folded into the
current run's counts. If no matching anchor is present, we return an empty
dict (defensive: avoid attributing arbitrary historical events to the
current run).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_tool_calls(
    session_log_path: Path,
    session_id: str,
) -> dict[str, int]:
    """Count toolCall events by tool name within the given session.

    Returns a dict mapping tool name -> invocation count, e.g.
    ``{"exec": 4, "read": 2}``. Returns an empty dict when:

    - the session log file does not exist (logs a warning),
    - the file is empty,
    - the file contains no ``session`` event with ``id == session_id``,
    - no toolCall blocks appear after the matching anchor.

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

    # Pass 2: find the LAST session anchor matching our session_id. "Last"
    # rather than "first" handles the (currently unobserved but possible)
    # case where the same id appears more than once in an append-only file.
    anchor_index: int | None = None
    for i, ev in enumerate(events):
        if ev.get("type") == "session" and ev.get("id") == session_id:
            anchor_index = i
    if anchor_index is None:
        logger.warning(
            "no session anchor matching id=%s in %s; tool_calls will be empty "
            "(events from other sessions in the same file are intentionally "
            "not counted)",
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
