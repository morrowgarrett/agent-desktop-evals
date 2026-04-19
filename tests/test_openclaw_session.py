"""Tests for OpenClaw session-log parsing (per-tool-call extraction).

The session log is an append-only JSONL written by OpenClaw at
~/.openclaw/agents/<agent_id>/sessions/<sessionId>.jsonl. Each event is a
JSON object on its own line. Top-level event types observed: 'message',
'custom', 'compaction', 'thinking_level_change', 'session', 'model_change'.

Tool calls live inside 'message' events with role 'assistant' — content is
an array of blocks; blocks of type 'toolCall' carry {id, name, arguments}.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_desktop_evals.runners._openclaw_session import extract_tool_calls


def test_extract_tool_calls_from_real_session_fragment(tmp_path: Path):
    """Anchored on a real session log fragment shape."""
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        json.dumps({"type": "session", "id": "test-sess-1",
                    "timestamp": "2026-04-19T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "u1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "user", "content": [
                          {"type": "text", "text": "do thing"}]}}) + "\n"
        + json.dumps({"type": "message", "id": "a1", "parentId": "u1",
                      "timestamp": "2026-04-19T00:00:02Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "text", "text": "ok"},
                          {"type": "toolCall", "id": "tc1", "name": "exec",
                           "arguments": "..."},
                      ]}}) + "\n"
        + json.dumps({"type": "message", "id": "tr1", "parentId": "a1",
                      "timestamp": "2026-04-19T00:00:03Z",
                      "message": {"role": "toolResult", "toolName": "exec",
                                  "content": []}}) + "\n"
        + json.dumps({"type": "message", "id": "a2", "parentId": "tr1",
                      "timestamp": "2026-04-19T00:00:04Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "tc2", "name": "exec",
                           "arguments": "..."},
                          {"type": "toolCall", "id": "tc3", "name": "read",
                           "arguments": "..."},
                      ]}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="test-sess-1")
    assert result == {"exec": 2, "read": 1}, f"got {result}"


def test_extract_tool_calls_missing_log_returns_empty(tmp_path: Path):
    """Missing session log file is non-fatal: empty dict, no crash."""
    result = extract_tool_calls(tmp_path / "does-not-exist.jsonl", session_id="x")
    assert result == {}


def test_extract_tool_calls_handles_malformed_json_lines(tmp_path: Path):
    """Skip lines that fail JSON decode; don't crash."""
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        '{"type": "session", "id": "s1", "timestamp": "2026-04-19T00:00:00Z"}\n'
        "not valid json\n"
        + json.dumps({"type": "message", "id": "a1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "x", "name": "exec",
                           "arguments": "..."}]}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="s1")
    assert result == {"exec": 1}, f"got {result}"


def test_extract_tool_calls_takes_only_events_after_session_anchor(tmp_path: Path):
    """Older events from prior runs in the same file are NOT counted.

    The JSONL file is append-only and may contain multiple distinct session
    anchors. We must only count toolCall blocks that follow the anchor with
    id == session_id.
    """
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        # Old run from days ago — should NOT be counted
        json.dumps({"type": "session", "id": "old-sess",
                    "timestamp": "2026-04-01T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "old1",
                      "timestamp": "2026-04-01T00:00:01Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "old", "name": "old_tool",
                           "arguments": "..."}]}}) + "\n"
        # Today's session
        + json.dumps({"type": "session", "id": "today-sess",
                      "timestamp": "2026-04-19T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "new1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "new", "name": "exec",
                           "arguments": "..."}]}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="today-sess")
    assert result == {"exec": 1}, f"old_tool should not be counted; got {result}"


def test_extract_tool_calls_ignores_non_assistant_messages(tmp_path: Path):
    """toolResult and user messages don't count as tool invocations.

    Only assistant 'toolCall' content blocks count. toolResult events have a
    'toolName' key but represent the *response* — not an invocation.
    """
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        json.dumps({"type": "session", "id": "s1",
                    "timestamp": "2026-04-19T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "u1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "user", "content": [
                          {"type": "text", "text": "go"}]}}) + "\n"
        + json.dumps({"type": "message", "id": "tr1",
                      "timestamp": "2026-04-19T00:00:02Z",
                      "message": {"role": "toolResult",
                                  "toolName": "exec",
                                  "content": []}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="s1")
    assert result == {}, f"non-assistant messages must not count, got {result}"


def test_extract_tool_calls_no_matching_session_returns_empty(tmp_path: Path):
    """If the file has session anchors but none match session_id, return empty.

    Defensive: avoids accidentally counting unrelated runs when the caller
    passes a session_id that's not in the file at all.
    """
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        json.dumps({"type": "session", "id": "other-sess",
                    "timestamp": "2026-04-19T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "a1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "x", "name": "exec",
                           "arguments": "..."}]}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="not-in-file")
    assert result == {}, (
        f"unmatched session_id must yield empty (no fall-through to all events); "
        f"got {result}"
    )


def test_extract_tool_calls_handles_empty_file(tmp_path: Path):
    """An empty session log is valid: zero tool calls, no crash."""
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("")
    result = extract_tool_calls(fixture, session_id="anything")
    assert result == {}


def test_extract_tool_calls_multiple_calls_same_tool(tmp_path: Path):
    """Multiple invocations of the same tool aggregate by name."""
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(
        json.dumps({"type": "session", "id": "s1",
                    "timestamp": "2026-04-19T00:00:00Z"}) + "\n"
        + json.dumps({"type": "message", "id": "a1",
                      "timestamp": "2026-04-19T00:00:01Z",
                      "message": {"role": "assistant", "content": [
                          {"type": "toolCall", "id": "1", "name": "exec",
                           "arguments": "..."},
                          {"type": "toolCall", "id": "2", "name": "exec",
                           "arguments": "..."},
                          {"type": "toolCall", "id": "3", "name": "exec",
                           "arguments": "..."},
                          {"type": "toolCall", "id": "4", "name": "exec",
                           "arguments": "..."},
                      ]}}) + "\n"
    )
    result = extract_tool_calls(fixture, session_id="s1")
    assert result == {"exec": 4}, f"got {result}"
