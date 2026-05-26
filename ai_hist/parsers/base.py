from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ai_hist.models import Event


class Parser(ABC):
    name: str
    display_name: str
    stable: bool = True   # False = not yet validated; skipped unless explicitly requested

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path.home()

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def parse(self) -> list[Event]: ...
