"""Noise analysis: flapping, never-acknowledged and duplicate alerts, and a score."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import median

from .loader import AlertEvent

# Weights of the noise score components. They add up to 100.
WEIGHT_UNACKED = 35   # share of firings nobody acknowledged
WEIGHT_FLAPPING = 25  # share of firings that are re-fires shortly after a resolve
WEIGHT_VOLUME = 25    # firings per day, saturating at VOLUME_SATURATION
WEIGHT_DUPLICATE = 15  # overlap with another alert that fires at the same moments
VOLUME_SATURATION = 5.0  # firings per day that count as "maximum volume"


@dataclass
class Settings:
    flap_gap_minutes: float = 30.0   # re-fire within this long of the last resolve = flap
    min_flaps: int = 3               # flaps needed before an alert is called flapping
    min_flap_ratio: float = 0.3      # ...and this share of its firings must be flaps
    min_firings: int = 3             # firings needed before "never acknowledged" applies
    low_ack_rate: float = 0.25       # at or below this an alert is "rarely acknowledged"
    duplicate_window_seconds: float = 120.0  # firings this close together co-occur
    min_duplicate_overlap: float = 0.8       # share of the rarer alert's firings that co-occur
    max_for_minutes: int = 15        # never suggest a for: longer than this


@dataclass
class AlertStats:
    key: str
    name: str
    service: str
    severity: str
    firings: int
    firings_per_day: float
    acknowledged: int
    ack_rate: float | None          # None when the export has no acknowledgement data
    flaps: int
    flap_ratio: float
    median_duration_minutes: float | None
    duplicate_of: str | None = None
    duplicate_overlap: float = 0.0
    noise_score: int = 0
    recommendation: str = "keep"
    reasons: list[str] = field(default_factory=list)
    suggested_for_minutes: int | None = None


@dataclass
class Audit:
    start: datetime | None
    end: datetime | None
    days: float
    total_firings: int
    has_ack_data: bool
    alerts: list[AlertStats]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = self.start.isoformat() if self.start else None
        data["end"] = self.end.isoformat() if self.end else None
        return data


def filter_recent(events: list[AlertEvent], days: float | None) -> list[AlertEvent]:
    """Keep events from the last ``days`` days, counted back from the newest event."""
    if not events or days is None:
        return events
    cutoff = max(e.fired_at for e in events) - timedelta(days=days)
    return [e for e in events if e.fired_at >= cutoff]


def count_flaps(events: list[AlertEvent], gap: timedelta) -> int:
    """Firings that start within ``gap`` of the previous firing's resolution."""
    flaps = 0
    for previous, current in zip(events, events[1:]):
        if previous.resolved_at is None:
            continue
        if timedelta(0) <= current.fired_at - previous.resolved_at <= gap:
            flaps += 1
    return flaps


def co_firings(a: list[AlertEvent], b: list[AlertEvent], window: float) -> int:
    """Number of firings in ``a`` matched one-to-one with a firing in ``b`` within ``window`` s."""
    matched, j = 0, 0
    for event in a:
        while j < len(b) and (event.fired_at - b[j].fired_at).total_seconds() > window:
            j += 1
        if j < len(b) and abs((b[j].fired_at - event.fired_at).total_seconds()) <= window:
            matched += 1
            j += 1
    return matched


def find_duplicates(groups: dict[str, list[AlertEvent]], settings: Settings) -> dict[str, tuple[str, float]]:
    """Map each redundant alert to (the alert it shadows, overlap ratio).

    Two alerts are duplicates when at least ``min_duplicate_overlap`` of the
    rarer alert's firings start within the duplicate window of a firing of the
    other. Of the pair, the one acknowledged less often (then the one that
    fires more, then the later name) is reported as the duplicate.
    """
    duplicates: dict[str, tuple[str, float]] = {}
    keys = sorted(groups)
    for i, first in enumerate(keys):
        for second in keys[i + 1:]:
            a, b = groups[first], groups[second]
            if min(len(a), len(b)) < settings.min_firings:
                continue
            overlap = co_firings(a, b, settings.duplicate_window_seconds) / min(len(a), len(b))
            if overlap < settings.min_duplicate_overlap:
                continue

            def rank(key: str) -> tuple:
                events = groups[key]
                acked = sum(1 for e in events if e.acknowledged_at) / len(events)
                return (acked, -len(events), [-ord(c) for c in key])

            keep, drop = sorted((first, second), key=rank, reverse=True)
            if drop not in duplicates or duplicates[drop][1] < overlap:
                duplicates[drop] = (keep, round(overlap, 2))
    return duplicates


def suggest_for_minutes(events: list[AlertEvent], settings: Settings) -> tuple[int, int] | None:
    """A ``for:`` duration that would have suppressed the short-lived firings.

    Returns (minutes, firings that resolved within that time), using the 75th
    percentile of resolved durations rounded up to a whole minute.
    """
    durations = sorted(e.duration_seconds for e in events if e.duration_seconds is not None)
    if not durations:
        return None
    p75 = durations[min(len(durations) - 1, math.ceil(0.75 * len(durations)) - 1)]
    minutes = min(settings.max_for_minutes, max(1, math.ceil(p75 / 60)))
    return minutes, sum(1 for d in durations if d <= minutes * 60)


def noise_score(stats: AlertStats) -> int:
    parts = [
        (WEIGHT_FLAPPING, stats.flap_ratio),
        (WEIGHT_VOLUME, min(1.0, stats.firings_per_day / VOLUME_SATURATION)),
        (WEIGHT_DUPLICATE, stats.duplicate_overlap),
    ]
    if stats.ack_rate is not None:
        parts.append((WEIGHT_UNACKED, 1.0 - stats.ack_rate))
    # Without acknowledgement data the remaining components are rescaled to 0-100.
    return round(100 * sum(w * v for w, v in parts) / sum(w for w, _ in parts))


def recommend(stats: AlertStats, events: list[AlertEvent], settings: Settings) -> None:
    """Fill in recommendation, reasons and suggested for-duration on ``stats``."""
    delete = tune = False
    if stats.ack_rate is not None and stats.firings >= settings.min_firings:
        if stats.acknowledged == 0:
            delete = True
            stats.reasons.append(f"never acknowledged in {stats.firings} firings")
        elif stats.ack_rate <= settings.low_ack_rate:
            tune = True
            stats.reasons.append(
                f"acknowledged only {stats.acknowledged} of {stats.firings} times; "
                "raise the threshold or lower the severity")
    if stats.duplicate_of:
        tune = True
        stats.reasons.append(
            f"fires together with {stats.duplicate_of} ({stats.duplicate_overlap:.0%} overlap); "
            "merge the two or inhibit this one")
    if stats.flaps >= settings.min_flaps and stats.flap_ratio >= settings.min_flap_ratio:
        tune = True
        reason = f"flapping: {stats.flaps} of {stats.firings} firings re-fired within " \
                 f"{settings.flap_gap_minutes:g} min of resolving"
        suggestion = suggest_for_minutes(events, settings)
        if suggestion:
            stats.suggested_for_minutes, suppressed = suggestion
            reason += (f"; `for: {suggestion[0]}m` would have suppressed "
                       f"{suppressed} of {stats.firings} firings")
        stats.reasons.append(reason)
    stats.recommendation = "delete" if delete else "tune" if tune else "keep"


def audit(events: list[AlertEvent], settings: Settings | None = None) -> Audit:
    settings = settings or Settings()
    if not events:
        return Audit(None, None, 0.0, 0, False, [])
    start = min(e.fired_at for e in events)
    end = max(e.fired_at for e in events)
    days = max(1.0, (end - start).total_seconds() / 86400)
    has_ack_data = any(e.acknowledged_at for e in events)

    groups: dict[str, list[AlertEvent]] = defaultdict(list)
    for event in sorted(events, key=lambda e: e.fired_at):
        groups[event.key].append(event)
    duplicates = find_duplicates(groups, settings)

    alerts = []
    for key, group in groups.items():
        durations = [e.duration_seconds for e in group if e.duration_seconds is not None]
        acked = sum(1 for e in group if e.acknowledged_at)
        flaps = count_flaps(group, timedelta(minutes=settings.flap_gap_minutes))
        duplicate_of, overlap = duplicates.get(key, (None, 0.0))
        stats = AlertStats(
            key=key, name=group[0].name, service=group[0].service,
            severity=group[-1].severity, firings=len(group),
            firings_per_day=round(len(group) / days, 2),
            acknowledged=acked,
            ack_rate=round(acked / len(group), 2) if has_ack_data else None,
            flaps=flaps, flap_ratio=round(flaps / len(group), 2),
            median_duration_minutes=round(median(durations) / 60, 1) if durations else None,
            duplicate_of=duplicate_of, duplicate_overlap=overlap,
        )
        stats.noise_score = noise_score(stats)
        recommend(stats, group, settings)
        alerts.append(stats)
    alerts.sort(key=lambda s: (-s.noise_score, -s.firings, s.key))
    return Audit(start, end, round(days, 1), len(events), has_ack_data, alerts)
