"""Command-line interface: ``alert-noise-auditor analyze FILE``."""

from __future__ import annotations

import argparse
import csv
import os
import sys

from . import __version__
from .analysis import Settings, audit, filter_recent
from .loader import AlertEvent, InputError, load_file, load_text
from .opsgenie import fetch_alerts
from .pagerduty import fetch_incidents
from .report import to_json, to_markdown


def build_parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(
        prog="alert-noise-auditor",
        description="Find flapping, never-acknowledged and duplicate alerts in an alert history export.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="analyze an alert history file")
    analyze.add_argument("file", metavar="FILE", nargs="?",
                         help="CSV or JSON alert history, or - for stdin (with --source file)")
    analyze.add_argument("--source", choices=("file", "pagerduty", "opsgenie"), default="file",
                         help="where to read history from (default: file). pagerduty reads "
                              "incidents with the token in PAGERDUTY_API_TOKEN; opsgenie reads "
                              "alerts with the key in OPSGENIE_API_KEY")
    analyze.add_argument("--pagerduty-service", action="append", default=[], metavar="ID",
                         help="only PagerDuty incidents of this service ID (repeatable)")
    analyze.add_argument("--opsgenie-query", default="", metavar="QUERY",
                         help="Opsgenie search query to narrow the alerts, e.g. 'teams:payments'")
    analyze.add_argument("--opsgenie-region", choices=("us", "eu"), default="us",
                         help="Opsgenie API region (default: us)")
    analyze.add_argument("--save", metavar="CSV",
                         help="also write the loaded history to CSV, to re-run offline later")
    analyze.add_argument("--input-format", choices=("auto", "csv", "json"), default="auto",
                         help="input format (default: from the file extension or content)")
    analyze.add_argument("--format", choices=("markdown", "json"), default="markdown",
                         help="output format (default: markdown)")
    analyze.add_argument("--days", type=float, metavar="N",
                         help="only use the last N days, counted back from the newest event "
                              "(with --source pagerduty or opsgenie: back from now, default 30)")
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


def save_csv(events: list[AlertEvent], path: str) -> None:
    """Write events in the CSV input format, so the file can be analysed again."""
    def stamp(moment):
        return moment.isoformat().replace("+00:00", "Z") if moment else ""

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "service", "severity", "fired_at", "acknowledged_at",
                         "resolved_at"])
        for e in events:
            writer.writerow([e.name, e.service, e.severity, stamp(e.fired_at),
                             stamp(e.acknowledged_at), stamp(e.resolved_at)])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source == "file" and args.file is None:
        parser.error("FILE is required with --source file")
    if args.source != "file" and args.file is not None:
        parser.error(f"FILE cannot be combined with --source {args.source}")
    where = args.file if args.source == "file" else args.source
    days = args.days if args.days is not None else 30.0
    try:
        if args.source == "opsgenie":
            events = fetch_alerts(os.environ.get("OPSGENIE_API_KEY", ""), days=days,
                                  query=args.opsgenie_query, region=args.opsgenie_region)
        elif args.source == "pagerduty":
            events = fetch_incidents(os.environ.get("PAGERDUTY_API_TOKEN", ""),
                                     days=days,
                                     service_ids=tuple(args.pagerduty_service))
        elif args.file == "-":
            events = load_text(sys.stdin.read(), args.input_format)
        else:
            events = load_file(args.file, args.input_format)
        if args.save:
            save_csv(events, args.save)
    except (OSError, UnicodeDecodeError, InputError) as error:
        print(f"alert-noise-auditor: {where}: {error}", file=sys.stderr)
        return 2

    settings = Settings(flap_gap_minutes=args.flap_gap,
                        duplicate_window_seconds=args.duplicate_window,
                        min_firings=args.min_firings)
    if args.source == "file":
        events = filter_recent(events, args.days)  # remote sources fetched only --days
    result = audit(events, settings)
    print(to_json(result) if args.format == "json" else to_markdown(result, args.top), end="")
    if args.format == "json":
        print()
    if args.fail_on_delete and any(a.recommendation == "delete" for a in result.alerts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
