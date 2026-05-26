from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


from ai_hist.parsers import ALL_PARSERS, PARSER_MAP
from ai_hist import timeline


def _parse_datetime(s: str) -> datetime:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Unrecognised datetime format: {s!r}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-hist",
        description="Forensic timeline of local AI tool activity.",
    )
    p.add_argument(
        "--tools",
        metavar="TOOL[,TOOL...]",
        help=f"Comma-separated tool names to scan. Available: {', '.join(PARSER_MAP)}",
    )
    p.add_argument(
        "--root",
        metavar="DIR",
        help=(
            "Treat DIR as the home directory for all path resolution. "
            "Use this when analysing a forensic image or a non-standard install location "
            "(e.g. --root /Volumes/evidence/Users/john)."
        ),
    )
    p.add_argument(
        "--file",
        metavar="FILE",
        dest="files",
        action="append",
        help=(
            "Parse a specific artifact file directly, bypassing normal discovery. "
            "Can be repeated. Accepts .jsonl session files, history.jsonl, "
            "state.vscdb (Cursor), state_5.sqlite or logs_2.sqlite (Codex). "
            "Example: --file /mnt/evidence/.claude/projects/foo/session.jsonl"
        ),
    )
    p.add_argument(
        "--since",
        type=_parse_datetime,
        metavar="DATETIME",
        help="Include events on or after this date/time (UTC). E.g. 2026-04-01 or 2026-04-01T09:00:00",
    )
    p.add_argument(
        "--until",
        type=_parse_datetime,
        metavar="DATETIME",
        help="Include events on or before this date/time (UTC).",
    )
    p.add_argument(
        "--role",
        choices=["user", "assistant", "system", "tool_use", "tool_result", "all"],
        default="all",
        help="Filter by message role (default: all).",
    )
    p.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text).",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        help="Write output to a file instead of stdout.",
    )
    p.add_argument(
        "--truncate",
        type=int,
        default=120,
        metavar="N",
        help="Text mode only: truncate content to N chars per line (default: 120, 0 = no truncate). JSON and CSV are never truncated.",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color codes in text output.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show metadata (cwd, model, branch) alongside each event.",
    )
    p.add_argument(
        "--list-tools",
        action="store_true",
        help="Show all supported tools and whether they are available on this system.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    if args.list_tools:
        print(f"{'Tool':<16}  {'Display Name':<22}  {'Available':<10}  Status")
        print("-" * 68)
        for cls in ALL_PARSERS:
            inst = cls()
            avail = "yes" if inst.is_available() else "no"
            status = "stable" if cls.stable else "incomplete"
            print(f"{inst.name:<16}  {inst.display_name:<22}  {avail:<10}  {status}")
        return 0

    tools = [t.strip() for t in args.tools.split(",")] if args.tools else None
    if tools:
        unknown = [t for t in tools if t not in PARSER_MAP]
        if unknown:
            print(f"Unknown tool(s): {', '.join(unknown)}. Available: {', '.join(PARSER_MAP)}", file=sys.stderr)
            return 1

    root = Path(args.root).expanduser().resolve() if args.root else None
    if root and not root.is_dir():
        print(f"--root path does not exist or is not a directory: {root}", file=sys.stderr)
        return 1

    files = [Path(f).expanduser().resolve() for f in args.files] if args.files else None
    if files and (tools or root):
        print("--file cannot be combined with --tools or --root", file=sys.stderr)
        return 1

    roles: set[str] | None = None if args.role == "all" else {args.role}

    events = timeline.collect(
        tools=tools,
        since=args.since,
        until=args.until,
        roles=roles,
        home=root,
        files=files,
    )

    out_file = None
    try:
        if args.output:
            out_file = open(args.output, "w", encoding="utf-8")
            out = out_file
        else:
            out = sys.stdout

        color = not args.no_color and args.format == "text" and os.isatty(sys.stdout.fileno())

        if not events:
            if args.format == "text":
                print("No events found matching the given filters.", file=sys.stderr)
            elif args.format == "json":
                out.write("[]\n")
            return 0

        if args.format == "text":
            if args.truncate > 0:
                print(
                    f"note: content truncated to {args.truncate} chars per line in text mode "
                    "(use --truncate 0 to disable, or --format json/csv for full content)",
                    file=sys.stderr,
                )
            timeline.render_text(events, out=out, truncate=args.truncate, color=color, verbose=args.verbose)
        elif args.format == "json":
            timeline.render_json(events, out=out)
        elif args.format == "csv":
            timeline.render_csv(events, out=out)

    finally:
        if out_file:
            out_file.close()

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
