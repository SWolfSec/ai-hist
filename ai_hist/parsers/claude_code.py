from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ai_hist.models import Event
from ai_hist.parsers.base import Parser

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {"user", "assistant"}


def _extract_text(content: str | list) -> str:
    """Flatten all human-readable text from a message content field.

    Handles plain strings, text blocks, thinking blocks, tool_use (extracts
    command/args), and tool_result payloads.
    """
    if isinstance(content, str):
        return content.strip()

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            parts.append(f"[thinking] {block.get('thinking', '')}")
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            if name == "Bash" and isinstance(inp, dict):
                cmd = inp.get("command", "")
                desc = inp.get("description", "")
                line = f"[tool: {name}] {desc}: {cmd}" if desc else f"[tool: {name}] {cmd}"
            elif isinstance(inp, dict):
                args = ", ".join(f"{k}={v!r}" for k, v in inp.items() if isinstance(v, str))
                line = f"[tool: {name}] {args}"
            else:
                line = f"[tool: {name}]"
            parts.append(line)
        elif btype == "tool_result":
            inner = block.get("content", "")
            if isinstance(inner, str):
                parts.append(f"[tool_result] {inner}")
            elif isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(f"[tool_result] {item.get('text', '')}")
    return "\n".join(p for p in parts if p).strip()


def _extract_user_text_only(content: str | list) -> str:
    """Extract only direct user-typed text, no tool results."""
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class ClaudeCodeParser(Parser):
    name = "claude_code"
    display_name = "Claude Code"

    @property
    def _projects_dir(self) -> Path:
        return self.home / ".claude" / "projects"

    @property
    def _history_file(self) -> Path:
        return self.home / ".claude" / "history.jsonl"

    def is_available(self) -> bool:
        return self._projects_dir.exists()

    def parse(self) -> list[Event]:
        events: list[Event] = []
        session_ids = set()

        jsonl_files = list(self._projects_dir.rglob("*.jsonl"))
        for path in jsonl_files:
            session_id = path.stem
            session_ids.add(session_id)
            events.extend(self._parse_session_file(path, session_id))

        events.extend(self._parse_history(known_sessions=session_ids))
        return events

    def _parse_session_file(self, path: Path, session_id: str) -> list[Event]:
        events: list[Event] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("JSON parse error %s line %d", path, lineno)
                        continue

                    rtype = record.get("type")
                    if rtype not in _CONTENT_TYPES:
                        continue

                    ts = _parse_ts(record.get("timestamp", ""))
                    if ts is None:
                        continue

                    msg = record.get("message", {})
                    if not isinstance(msg, dict):
                        continue

                    role = msg.get("role", rtype)

                    # For user turns, separate direct text from tool result payloads.
                    # Tool results are returned by the harness, not typed by the user;
                    # emitting them as "user" events is misleading in a forensic view.
                    raw_content = msg.get("content", "")
                    if role == "user":
                        user_text = _extract_user_text_only(raw_content)
                        tool_text = _extract_tool_results(raw_content)
                        if user_text:
                            events.append(_make_event(ts, self.name, "user", user_text, session_id, path, record, msg))
                        if tool_text:
                            events.append(_make_event(ts, self.name, "tool_result", tool_text, session_id, path, record, msg))
                    else:
                        content = _extract_text(raw_content)
                        if content:
                            events.append(_make_event(ts, self.name, role, content, session_id, path, record, msg))

        except OSError as e:
            logger.warning("Cannot read %s: %s", path, e)
        return events

    def _parse_history(self, known_sessions: set[str]) -> list[Event]:
        """Parse ~/.claude/history.jsonl.

        Skips entries whose sessionId already has a full JSONL file, since those
        prompts appear there verbatim. Only surfaces prompts from sessions whose
        JSONL file has been deleted or pruned.
        """
        if not self._history_file.exists():
            return []
        events: list[Event] = []
        try:
            with self._history_file.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    session_id = record.get("sessionId", "")
                    if session_id in known_sessions:
                        continue

                    ts_ms = record.get("timestamp")
                    if not isinstance(ts_ms, (int, float)):
                        continue
                    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

                    display = record.get("display", "").strip()
                    if not display:
                        continue

                    meta: dict = {}
                    if "project" in record:
                        meta["project"] = record["project"]

                    events.append(Event(
                        timestamp=ts,
                        tool=self.name,
                        role="user",
                        content=display,
                        session_id=session_id,
                        source_file=self._history_file,
                        metadata=meta,
                    ))
        except OSError as e:
            logger.warning("Cannot read history.jsonl: %s", e)
        return events


def _extract_tool_results(content: str | list) -> str:
    if isinstance(content, str):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        inner = block.get("content", "")
        if isinstance(inner, str):
            parts.append(inner)
        elif isinstance(inner, list):
            for item in inner:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _make_event(
    ts: datetime,
    tool: str,
    role: str,
    content: str,
    session_id: str,
    path: Path,
    record: dict,
    msg: dict,
) -> Event:
    meta: dict = {}
    for key in ("cwd", "gitBranch", "version", "permissionMode"):
        if key in record:
            meta[key] = record[key]
    if "model" in msg:
        meta["model"] = msg["model"]
    return Event(
        timestamp=ts,
        tool=tool,
        role=role,
        content=content,
        session_id=session_id,
        source_file=path,
        metadata=meta,
    )
