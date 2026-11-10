"""Render an audit as Markdown or JSON."""

from __future__ import annotations

import json

from .analysis import Audit, AlertStats


def to_json(audit: Audit) -> str:
    return json.dumps(audit.to_dict(), indent=2)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _row(stats: AlertStats) -> str:
    duration = "n/a" if stats.median_duration_minutes is None else f"{stats.median_duration_minutes:g}m"
    return (f"| {stats.key} | {stats.noise_score} | {stats.recommendation.upper()} | "
            f"{stats.firings} | {_percent(stats.ack_rate)} | {_percent(stats.flap_ratio)} | "
            f"{duration} | {stats.duplicate_of or '-'} |")


def to_markdown(audit: Audit, top: int | None = None) -> str:
    if not audit.alerts:
        return "# Alert noise audit\n\nNo alert events in the input.\n"
    counts = {r: sum(1 for a in audit.alerts if a.recommendation == r)
              for r in ("delete", "tune", "keep")}
    noisy_firings = sum(a.firings for a in audit.alerts if a.recommendation != "keep")
    out = [
        "# Alert noise audit",
        "",
        f"{audit.total_firings} firings of {len(audit.alerts)} alerts between "
        f"{audit.start:%Y-%m-%d} and {audit.end:%Y-%m-%d} ({audit.days:g} days).",
        "",
        f"**Recommendations:** delete {counts['delete']}, tune {counts['tune']}, keep {counts['keep']}. "
        f"Alerts marked delete or tune produced {noisy_firings} of {audit.total_firings} firings "
        f"({noisy_firings / audit.total_firings:.0%}).",
        "",
    ]
    if not audit.has_ack_data:
        out += ["Note: the input has no acknowledgement timestamps, so never-acknowledged "
                "alerts cannot be detected and the score uses the other signals only.", ""]
    shown = audit.alerts if top is None else audit.alerts[:top]
    out += [
        "| Alert | Noise score | Recommendation | Firings | Ack rate | Flap rate | Median duration | Duplicate of |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *(_row(a) for a in shown),
        "",
    ]
    if len(shown) < len(audit.alerts):
        out += [f"Showing the {len(shown)} noisiest of {len(audit.alerts)} alerts.", ""]
    for recommendation, title in (("delete", "Delete"), ("tune", "Tune")):
        selected = [a for a in shown if a.recommendation == recommendation]
        if not selected:
            continue
        out += [f"## {title}", ""]
        for stats in selected:
            out.append(f"- **{stats.key}** (score {stats.noise_score})")
            out += [f"  - {reason}" for reason in stats.reasons]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
