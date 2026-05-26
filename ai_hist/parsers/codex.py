from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ai_hist.models import Event
from ai_hist.parsers.base import Parser

logger = logging.getLogger(__name__)


def _parse_ts(val: int | float | str | None) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val / 1000 if val > 1e10 else val, tz=timezone.utc)
        except (OSError, ValueError):
            return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class CodexParser(Parser):
    name = "codex"
    display_name = "Codex CLI"
    stable = False

    @property
    def _base(self) -> Path:
        return self.home / ".codex"

    def is_available(self) -> bool:
        return (self._base / "state_5.sqlite").exists()

    def parse(self) -> list[Event]:
        events: list[Event] = []
        events.extend(self._parse_threads())
        events.extend(self._parse_logs())
        return events

    def _parse_threads(self) -> list[Event]:
        db = self._base / "state_5.sqlite"
        if not db.exists():
            return []
        events: list[Event] = []
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                rows = conn.execute(
                    "SELECT id, title, first_user_message, model, cwd, created_at FROM threads"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning("Cannot read Codex state DB: %s", e)
            return []

        for thread_id, title, first_msg, model, cwd, created_ts in rows:
            ts = _parse_ts(created_ts)
            if ts is None:
                continue
            content = first_msg or title or ""
            if not content.strip():
                continue
            meta: dict = {}
            if model:
                meta["model"] = model
            if cwd:
                meta["cwd"] = cwd
            events.append(Event(
                timestamp=ts,
                tool=self.name,
                role="user",
                content=content.strip(),
                session_id=str(thread_id),
                source_file=db,
                metadata=meta,
            ))
        return events

    def _parse_logs(self) -> list[Event]:
        db = self._base / "logs_2.sqlite"
        if not db.exists():
            return []
        events: list[Event] = []
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                # The logs table may have a feedback_log_body column with JSON content
                cols = [
                    row[1]
                    for row in conn.execute("PRAGMA table_info(logs)").fetchall()
                ]
                if not cols:
                    return []
                has_body = "feedback_log_body" in cols
                ts_col = "created_at" if "created_at" in cols else None
                if not has_body or ts_col is None:
                    return []
                rows = conn.execute(
                    f"SELECT {ts_col}, feedback_log_body FROM logs"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning("Cannot read Codex logs DB: %s", e)
            return []

        for created_ts, body in rows:
            ts = _parse_ts(created_ts)
            if ts is None or not body:
                continue
            try:
                data = json.loads(body) if isinstance(body, str) else {}
                content = data.get("content") or data.get("text") or str(body)
            except (json.JSONDecodeError, TypeError):
                content = str(body)
            content = content.strip()
            if not content:
                continue
            events.append(Event(
                timestamp=ts,
                tool=self.name,
                role="system",
                content=content,
                session_id="",
                source_file=db,
                metadata={},
            ))
        return events
