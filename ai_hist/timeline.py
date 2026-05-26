from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

_WHITESPACE = re.compile(r"\s+")

from ai_hist.models import Event
from ai_hist.parsers import ALL_PARSERS, PARSER_MAP
from ai_hist.parsers.claude_code import ClaudeCodeParser
from ai_hist.parsers.cursor import CursorParser
from ai_hist.parsers.codex import CodexParser

logger = logging.getLogger(__name__)

# Map known filenames/patterns to the parser that handles them
_FILE_DISPATCH: list[tuple[str, str]] = [
    ("history.jsonl",   "claude_code"),
    (".jsonl",          "claude_code"),
    ("state.vscdb",     "cursor"),
    ("state_5.sqlite",  "codex"),
    ("logs_2.sqlite",   "codex"),
]

_ROLE_COLOR = {
    "user":        "\033[94m",   # blue
    "assistant":   "\033[92m",   # green
    "system":      "\033[90m",   # dark gray
    "tool_use":    "\033[93m",   # yellow
    "tool_result": "\033[96m",   # cyan
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"
_DIM   = "\033[2m"

_TOOL_WIDTH    = 12
_ROLE_WIDTH    = 11   # "tool_result" is 11 chars
_SESSION_WIDTH = 10   # "[xxxxxxxx]"


def _detect_parser_name(path: Path) -> str | None:
    name = path.name
    for suffix, parser_name in _FILE_DISPATCH:
        if name == suffix or name.endswith(suffix):
            return parser_name
    return None


def parse_files(paths: list[Path]) -> list[Event]:
    """Parse a list of explicit file paths, auto-detecting the parser for each."""
    events: list[Event] = []
    for path in paths:
        if not path.exists():
            logger.warning("File not found: %s", path)
            continue

        parser_name = _detect_parser_name(path)
        if parser_name is None:
            logger.warning(
                "Cannot detect parser for %s (expected .jsonl, state.vscdb, "
                "state_5.sqlite, or logs_2.sqlite)",
                path,
            )
            continue

        if parser_name == "claude_code":
            parser = ClaudeCodeParser()
            if path.name == "history.jsonl":
                events.extend(parser._parse_history(known_sessions=set()))
            else:
                events.extend(parser._parse_session_file(path, path.stem))

        elif parser_name == "cursor":
            parser = CursorParser()
            events.extend(parser._parse_db(path))

        elif parser_name == "codex":
            parser = CodexParser()
            if path.name == "state_5.sqlite":
                # Point the parser at the parent directory as its base
                parser.home = path.parent.parent
                events.extend(parser._parse_threads())
            elif path.name == "logs_2.sqlite":
                parser.home = path.parent.parent
                events.extend(parser._parse_logs())

    return events


def collect(
    tools: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    roles: set[str] | None = None,
    home: Path | None = None,
    files: list[Path] | None = None,
) -> list[Event]:
    events: list[Event] = []

    if files:
        events.extend(parse_files(files))
    else:
        if tools:
            parsers = []
            for t in tools:
                if t not in PARSER_MAP:
                    continue
                cls = PARSER_MAP[t]
                if not cls.stable:
                    print(
                        f"warning: {cls.display_name} parser ({t!r}) is not yet validated "
                        "and may return incomplete or incorrect results",
                        file=sys.stderr,
                    )
                parsers.append(cls(home=home))
        else:
            parsers = [cls(home=home) for cls in ALL_PARSERS if cls.stable]

        for parser in parsers:
            if not parser.is_available():
                continue
            events.extend(parser.parse())

    if since:
        events = [e for e in events if e.timestamp >= since]
    if until:
        events = [e for e in events if e.timestamp <= until]
    if roles:
        events = [e for e in events if e.role in roles]

    events.sort()
    return events


def render_text(
    events: list[Event],
    out: TextIO = sys.stdout,
    truncate: int = 120,
    color: bool = True,
    verbose: bool = False,
) -> None:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    term_width = shutil.get_terminal_size((120, 40)).columns

    # Fixed-width prefix: "2026-04-19 21:39:33  claude_code   tool_result  [xxxxxxxx]  "
    # 19 + 2 + 12 + 2 + 11 + 2 + 10 + 2 = 60 chars of chrome
    _PREFIX_LEN = 19 + 2 + _TOOL_WIDTH + 2 + _ROLE_WIDTH + 2 + _SESSION_WIDTH + 2

    # How many chars of content to show; fill the rest of the terminal
    content_width = max(40, term_width - _PREFIX_LEN) if truncate == 0 else truncate

    prev_date = None
    for event in events:
        date_str = event.timestamp.strftime("%Y-%m-%d")
        if date_str != prev_date:
            sep = "─" * min(term_width, 80)
            out.write(f"\n{c(_BOLD, sep)}\n")
            prev_date = date_str

        ts    = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        tool  = event.tool.ljust(_TOOL_WIDTH)
        role  = event.role.ljust(_ROLE_WIDTH)
        sess  = f"[{event.short_session()}]".ljust(_SESSION_WIDTH)

        role_c = c(_ROLE_COLOR.get(event.role, ""), role)
        sess_c = c(_DIM, sess)

        single_line = _WHITESPACE.sub(" ", event.content).strip()

        if content_width > 0 and len(single_line) > content_width:
            snippet = single_line[:content_width - 1] + "…"
        else:
            snippet = single_line

        line = f"{ts}  {c(_BOLD, tool)}  {role_c}  {sess_c}  {snippet}"
        out.write(line + "\n")

        if verbose and event.metadata:
            meta_items = "  ".join(f"{k}={v}" for k, v in event.metadata.items())
            out.write(f"           {c(_DIM, meta_items)}\n")

    out.write("\n")


def render_json(events: list[Event], out: TextIO = sys.stdout) -> None:
    json.dump([e.as_dict() for e in events], out, indent=2, default=str)
    out.write("\n")


def render_csv(events: list[Event], out: TextIO = sys.stdout) -> None:
    writer = csv.writer(out)
    writer.writerow(["timestamp", "tool", "role", "session_id", "source_file", "content"])
    for e in events:
        writer.writerow([
            e.timestamp.isoformat(),
            e.tool,
            e.role,
            e.session_id,
            str(e.source_file),
            e.content,
        ])
