"""Command-line interface: ``alert-noise-auditor analyze FILE``."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .analysis import Settings, audit, filter_recent
from .loader import InputError, load_file, load_text
from .report import to_json, to_markdown


def build_parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(
        prog="alert-noise-auditor",
        description="Find flapping, never-acknowledged and duplicate alerts in an alert history export.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="analyze an alert history file")
    analyze.add_argument("file", metavar="FILE", help="CSV or JSON alert history, or - for stdin")
    analyze.add_argument("--input-format", choices=("auto", "csv", "json"), default="auto",
                         help="input format (default: from the file extension or content)")
    analyze.add_argument("--format", choices=("markdown", "json"), default="markdown",
                         help="output format (default: markdown)")
    analyze.add_argument("--days", type=float, metavar="N",
                         help="only use the last N days, counted back from the newest event")
    analyze.add_argument("--top", type=int, metavar="N",
                         help="show only the N noisiest alerts in the Markdown report")
    analyze.add_argument("--flap-gap", type=float, default=defaults.flap_gap_minutes, metavar="MIN",
                         help="a firing within MIN minutes of the previous resolve is a flap "
                              f"(default: {defaults.flap_gap_minutes:g})")
    analyze.add_argument("--duplicate-window", type=float, default=defaults.duplicate_window_seconds,
                         metavar="SEC",
                         help="firings of two alerts within SEC seconds co-occur "
                              f"(default: {defaults.duplicate_window_seconds:g})")
    analyze.add_argument("--min-firings", type=int, default=defaults.min_firings, metavar="N",
                         help="firings needed before an alert can be called never-acknowledged "
                              f"or duplicate (default: {defaults.min_firings})")
    analyze.add_argument("--fail-on-delete", action="store_true",
                         help="exit with status 1 if any alert is recommended for deletion")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.file == "-":
            events = load_text(sys.stdin.read(), args.input_format)
        else:
            events = load_file(args.file, args.input_format)
    except (OSError, UnicodeDecodeError, InputError) as error:
        print(f"alert-noise-auditor: {args.file}: {error}", file=sys.stderr)
        return 2

    settings = Settings(flap_gap_minutes=args.flap_gap,
                        duplicate_window_seconds=args.duplicate_window,
                        min_firings=args.min_firings)
    result = audit(filter_recent(events, args.days), settings)
    print(to_json(result) if args.format == "json" else to_markdown(result, args.top), end="")
    if args.format == "json":
        print()
    if args.fail_on_delete and any(a.recommendation == "delete" for a in result.alerts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
