from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Event:
    timestamp: datetime
    tool: str
    role: str           # user | assistant | system | tool_use | tool_result
    content: str
    session_id: str
    source_file: Path
    metadata: dict = field(default_factory=dict)

    def __lt__(self, other: "Event") -> bool:
        return self.timestamp < other.timestamp

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timestamp == other.timestamp

    def short_session(self) -> str:
        return self.session_id[:8] if len(self.session_id) >= 8 else self.session_id

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "tool": self.tool,
            "role": self.role,
            "content": self.content,
            "session_id": self.session_id,
            "source_file": str(self.source_file),
            **self.metadata,
        }
