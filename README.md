# ai-hist

Forensic timeline tool for local AI artifact analysis. Parses conversation history stored on disk by Claude Code and Cursor IDE, then produces a chronological event log with full timestamps.

```
2026-05-25 01:15:16  claude_code   tool_use     [a1b2c3d4]  [tool: Bash] cd /Users/alice/Projects/MyApp && xcodegen generate
2026-05-25 01:16:06  claude_code   tool_result  [a1b2c3d4]  ⚙️ Writing project... Created project at /Users/alice/Projects/MyApp
2026-05-25 01:16:09  claude_code   assistant    [a1b2c3d4]  [tool: Bash] xcodebuild -scheme MyApp build...
2026-05-25 01:20:48  claude_code   tool_result  [a1b2c3d4]  ** BUILD SUCCEEDED **
```

This was inspired by the research and project here: [https://github.com/xFreed0m/ghosttype](https://github.com/xFreed0m/ghosttype). But targetting forensic timelining rather then capturing stored credentials.

Note: This was tested on macOS. The tool should run on Linux but all storage paths are macOS specific. Using the `--root` should help identify where the  proper files exist. 

## Where it looks

| Tool | Storage location |
|------|-----------------|
| Claude Code | `~/.claude/projects/**/*.jsonl`, `~/.claude/history.jsonl` |
| Cursor IDE | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` + all `workspaceStorage/*/state.vscdb` |

`ai-hist --list-tools` shows which are present on the current machine and the status of each parser.

## Running it

**No install** — from the project directory:

```bash
git clone https://github.com/SwolfSec/ai-hist
cd ai-hist          # the project root, not the ai_hist/ subdirectory
python3 -m ai_hist --list-tools
```

**Manual venv** if you prefer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ai-hist --list-tools
```

## Usage

```bash
# Full timeline, all tools
ai-hist

# Only what the user typed
ai-hist --role user

# Only tool calls (what the AI actually ran)
ai-hist --role tool_use

# Narrow to a date range
ai-hist --since 2026-04-01 --until 2026-04-30

# Write to a file (json and csv write full content without truncation)
ai-hist --format json --output timeline.json
ai-hist --format csv  --output timeline.csv

# Full content in the terminal
ai-hist --truncate 0

# Show cwd, model, git branch alongside each event
ai-hist --verbose
```

## Mounted images and specific files

`--root DIR` replaces `~` in all path resolution. Point it at the subject's home directory on a mounted image:

```bash
ai-hist --root /Volumes/evidence/Users/john --format json --output john_timeline.json
```

`--file FILE` parses a single artifact directly, bypassing discovery. The parser is inferred from the filename:

| Filename | Parsed as |
|----------|-----------|
| `*.jsonl` | Claude Code session |
| `history.jsonl` | Claude Code global history |
| `state.vscdb` | Cursor IDE |
| `state_5.sqlite` | Codex CLI threads |
| `logs_2.sqlite` | Codex CLI logs |

```bash
ai-hist --file /mnt/case/state.vscdb --file /mnt/case/history.jsonl
ai-hist --file /mnt/case/.claude/projects/foo/session.jsonl --format json
```

`--file` and `--root`/`--tools` are mutually exclusive.

## Event roles

| Role | What it is |
|------|-----------|
| `user` | Text the human typed |
| `assistant` | The AI's natural language response |
| `tool_use` | A tool the AI invoked: Bash commands, file reads and writes, web searches |
| `tool_result` | What came back from the tool: stdout, file contents, build output |
| `system` | Harness bookkeeping: permission grants, interrupt signals |

`--role tool_use` is the fastest way to see every shell command and file operation the AI ran across all sessions. The default shows everything.

## Output formats

**`text`** (default): one line per event, content truncated to 120 chars. `--truncate 0` disables that; `--no-color` for piping.

**`json`** and **`csv`** write the full `content` field without cutting. The `json` output is an array of objects; `csv` columns are `timestamp, tool, role, session_id, source_file, content`.

## Planned

Codex CLI (`~/.codex/`) and ChatGPT Desktop (`~/Library/Application Support/com.openai.chat/`) parsers are written but not yet validated against real installs. They are excluded from default runs. You can invoke them explicitly with `--tools codex` or `--tools chatgpt` but expect gaps.

ChatGPT Desktop is additionally complicated by AES-128-CBC encryption with a key stored in macOS Keychain. Decryption is implemented via the optional `cryptography` dependency but untested:

```bash
pip install -e ".[chatgpt-decrypt]"
```

On a forensic image (not the original machine) the Keychain key won't be accessible regardless.

Additional OS support: add support for Linux and Windows. 

## Known gaps

Claude Desktop stores data under `~/Library/Application Support/Claude/` but the conversation format hasn't been reversed yet, so it's excluded entirely.

`history.jsonl` is deduplicated against session files. If a session's `.jsonl` exists, its prompts come from there and the history entry is skipped so nothing appears twice.

