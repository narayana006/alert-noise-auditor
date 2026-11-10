"""Load alert history from CSV or JSON into a list of ``AlertEvent``.

One event is one firing of one alert. Three input shapes are accepted:

* CSV with a header row (see ``FIELD_ALIASES`` for accepted column names)
* JSON array of flat objects using the same field names
* Alertmanager-style JSON: an array of alerts (as returned by
  ``GET /api/v2/alerts``) or a webhook payload ``{"alerts": [...]}``, where each
  alert has ``labels.alertname``, ``startsAt`` and ``endsAt``
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Canonical field -> accepted column / key names (matched case-insensitively).
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "alertname", "alert_name", "alert", "title"),
    "service": ("service", "job", "component"),
    "severity": ("severity", "priority", "urgency"),
    "fired_at": ("fired_at", "startsat", "started_at", "triggered_at", "created_at"),
    "acknowledged_at": ("acknowledged_at", "acked_at", "ack_at"),
    "resolved_at": ("resolved_at", "endsat", "ended_at", "closed_at"),
}
_FRACTION_RE = re.compile(r"\.(\d+)")


class InputError(ValueError):
    """The input file is not in a supported shape."""


@dataclass(frozen=True)
class AlertEvent:
    name: str
    fired_at: datetime
    service: str = ""
    severity: str = ""
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def key(self) -> str:
        """Identity of the alert rule: the name, qualified by service if known."""
        return f"{self.name} [{self.service}]" if self.service else self.name

    @property
    def duration_seconds(self) -> float | None:
        if self.resolved_at is None:
            return None
        return max(0.0, (self.resolved_at - self.fired_at).total_seconds())


def parse_time(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp. Empty values and Alertmanager's zero time give None.

    Timestamps without a UTC offset are taken to be UTC.
    """
    text = str(value or "").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    if text[-1] in "zZ":
        text = text[:-1] + "+00:00"
    # datetime.fromisoformat on older Pythons needs exactly 3 or 6 fraction digits.
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as error:
        raise InputError(f"cannot parse timestamp {value!r}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_flat(record: dict, where: str) -> AlertEvent:
    lowered = {str(k).strip().lower(): v for k, v in record.items() if k is not None}
    values: dict[str, object] = {}
    for field, aliases in FIELD_ALIASES.items():
        values[field] = next((lowered[a] for a in aliases if lowered.get(a) not in (None, "")), "")
    name = str(values["name"]).strip()
    if not name:
        raise InputError(f"{where}: missing alert name")
    try:
        fired_at = parse_time(values["fired_at"])
        acknowledged_at = parse_time(values["acknowledged_at"])
        resolved_at = parse_time(values["resolved_at"])
    except InputError as error:
        raise InputError(f"{where}: {error}") from error
    if fired_at is None:
        raise InputError(f"{where}: missing fired_at")
    return AlertEvent(name, fired_at, str(values["service"]).strip(),
                      str(values["severity"]).strip().lower(), acknowledged_at, resolved_at)


def _from_alertmanager(alert: dict, where: str) -> AlertEvent:
    labels = alert.get("labels") or {}
    flat = {
        "name": labels.get("alertname", ""),
        "service": labels.get("service") or labels.get("job") or "",
        "severity": labels.get("severity", ""),
        "fired_at": alert.get("startsAt", ""),
        "resolved_at": alert.get("endsAt", ""),
        # Alertmanager does not record acknowledgements; some exporters add one.
        "acknowledged_at": alert.get("acknowledgedAt") or alert.get("acknowledged_at") or "",
    }
    event = _from_flat(flat, where)
    # A still-firing alert carries an endsAt in the future (its resolve timeout).
    status = alert.get("status")
    state = status.get("state") if isinstance(status, dict) else status
    if state in ("firing", "active"):
        event = AlertEvent(event.name, event.fired_at, event.service, event.severity,
                           event.acknowledged_at, None)
    return event


def load_json(text: str) -> list[AlertEvent]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise InputError(f"invalid JSON: {error}") from error
    if isinstance(data, dict):
        data = data.get("alerts")
    if not isinstance(data, list):
        raise InputError('JSON input must be an array of alerts or an object with an "alerts" array')
    events = []
    for index, record in enumerate(data):
        where = f"record {index + 1}"
        if not isinstance(record, dict):
            raise InputError(f"{where}: expected an object")
        if isinstance(record.get("labels"), dict):
            events.append(_from_alertmanager(record, where))
        else:
            events.append(_from_flat(record, where))
    return events


def load_csv(text: str) -> list[AlertEvent]:
    reader = csv.DictReader(io.StringIO(text))
    events = []
    for row in reader:
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue  # blank line
        events.append(_from_flat(row, f"line {reader.line_num}"))
    return events


def load_text(text: str, fmt: str = "auto") -> list[AlertEvent]:
    """Parse ``text`` as ``csv`` or ``json``; ``auto`` looks at the first character."""
    text = text.lstrip("﻿")
    if fmt == "auto":
        fmt = "json" if text.lstrip()[:1] in ("[", "{") else "csv"
    events = load_json(text) if fmt == "json" else load_csv(text)
    return sorted(events, key=lambda e: e.fired_at)


def load_file(path: str, fmt: str = "auto") -> list[AlertEvent]:
    if fmt == "auto" and Path(path).suffix.lower() in (".csv", ".json"):
        fmt = Path(path).suffix.lower()[1:]
    return load_text(Path(path).read_text(encoding="utf-8"), fmt)
