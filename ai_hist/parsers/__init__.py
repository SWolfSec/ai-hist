from ai_hist.parsers.chatgpt import ChatGPTParser
from ai_hist.parsers.claude_code import ClaudeCodeParser
from ai_hist.parsers.codex import CodexParser
from ai_hist.parsers.cursor import CursorParser

ALL_PARSERS = [
    ClaudeCodeParser,
    CursorParser,
    CodexParser,
    ChatGPTParser,
]

PARSER_MAP = {p.name: p for p in ALL_PARSERS}
