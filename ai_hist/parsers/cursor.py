from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ai_hist.models import Event
from ai_hist.parsers.base import Parser

logger = logging.getLogger(__name__)

_BUBBLE_ROLE = {1: "user", 2: "assistant"}


def _parse_ts(val: str | int | float | None) -> datetime | None:
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


class CursorParser(Parser):
    name = "cursor"
    display_name = "Cursor IDE"

    @property
    def _global_db(self) -> Path:
        return (
            self.home
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )

    @property
    def _workspace_root(self) -> Path:
        return (
            self.home
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "workspaceStorage"
        )

    def is_available(self) -> bool:
        return self._global_db.exists()

    def parse(self) -> list[Event]:
        events: list[Event] = []
        dbs = [self._global_db]
        if self._workspace_root.exists():
            dbs.extend(self._workspace_root.rglob("state.vscdb"))
        for db_path in dbs:
            events.extend(self._parse_db(db_path))
        return events

    def _parse_db(self, db_path: Path) -> list[Event]:
        events: list[Event] = []
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                # Fetch all bubble records. Key format: bubbleId:<composerId>:<bubbleId>
                # Group by composerId so we can assign session IDs correctly.
                bubble_rows = conn.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
                ).fetchall()

                # Map composerId -> list of (ts, role, text) tuples
                by_composer: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)

                for bkey, bval in bubble_rows:
                    parts = bkey.split(":", 2)
                    if len(parts) != 3:
                        continue
                    composer_id = parts[1]
                    try:
                        bubble = json.loads(bval)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    btype = bubble.get("type")
                    role = _BUBBLE_ROLE.get(btype)
                    if role is None:
                        continue

                    text = bubble.get("text", "")
                    if not isinstance(text, str) or not text.strip():
                        continue

                    ts = _parse_ts(bubble.get("createdAt"))
                    if ts is None:
                        continue

                    by_composer[composer_id].append((ts, role, text.strip()))

                for composer_id, messages in by_composer.items():
                    messages.sort(key=lambda x: x[0])
                    for ts, role, text in messages:
                        events.append(Event(
                            timestamp=ts,
                            tool=self.name,
                            role=role,
                            content=text,
                            session_id=composer_id,
                            source_file=db_path,
                            metadata={},
                        ))
        except sqlite3.Error as e:
            logger.warning("Cannot read Cursor DB %s: %s", db_path, e)
        return events
