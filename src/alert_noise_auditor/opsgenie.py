"""Fetch alert history from the Opsgenie Alert API (v2) as ``AlertEvent`` records.

Only read-only ``GET /v2/alerts`` requests are made, sorted newest first, and
paging stops at the first alert created before the window.

One Opsgenie alert becomes one firing:

* the alert ``message`` is the alert name
* a ``service:<name>`` (or ``service=<name>``) tag is the service, if present
* ``priority`` (P1-P5) is the severity
* ``report.ackTime`` / ``report.closeTime`` (milliseconds after creation) give the
  acknowledged and resolved times
"""

from __future__ import annotations

import functools
import urllib.parse
from datetime import datetime, timedelta, timezone

from .loader import AlertEvent, parse_time
from .pagerduty import Fetch, SourceError, http_get_json

API_URLS = {"us": "https://api.opsgenie.com", "eu": "https://api.eu.opsgenie.com"}
PAGE_SIZE = 100
# Opsgenie rejects list requests where offset + limit exceeds 20000.
MAX_OFFSET = 20_000
OPSGENIE_HINTS = {401: " (check OPSGENIE_API_KEY)",
                  403: " (the key lacks the Read access right)",
                  422: " (Opsgenie rejected the request; check --opsgenie-query)",
                  429: " (rate limited; try again shortly or narrow --days)"}

opsgenie_get_json = functools.partial(http_get_json, label="Opsgenie", hints=OPSGENIE_HINTS)


def _service(tags: object) -> str:
    for tag in tags if isinstance(tags, list) else []:
        text = str(tag)
        for sep in (":", "="):
            prefix = "service" + sep
            if text.lower().startswith(prefix):
                return text[len(prefix):].strip()
    return ""


def _after(start: datetime, millis: object) -> datetime | None:
    if isinstance(millis, bool) or not isinstance(millis, (int, float)) or millis < 0:
        return None
    return start + timedelta(milliseconds=millis)


def to_event(alert: dict, where: str) -> AlertEvent:
    """Map one alert object from ``GET /v2/alerts`` to an ``AlertEvent``."""
    name = str(alert.get("message") or "").strip()
    fired_at = parse_time(alert.get("createdAt"))
    if not name or fired_at is None:
        raise SourceError(f"alert {alert.get('tinyId') or where}: missing message or createdAt")
    report = alert.get("report") if isinstance(alert.get("report"), dict) else {}
    updated = parse_time(alert.get("updatedAt"))

    acknowledged_at = _after(fired_at, report.get("ackTime"))
    if acknowledged_at is None and alert.get("acknowledged"):
        acknowledged_at = updated or fired_at  # acked, exact time not reported
    resolved_at = None
    if alert.get("status") == "closed":
        resolved_at = _after(fired_at, report.get("closeTime")) or updated
    return AlertEvent(
        name=name,
        fired_at=fired_at,
        service=_service(alert.get("tags")),
        severity=str(alert.get("priority") or "").strip().lower(),
        acknowledged_at=acknowledged_at,
        resolved_at=resolved_at,
    )


def fetch_alerts(api_key: str, days: float = 30.0, query: str = "", region: str = "us",
                 now: datetime | None = None, fetch: Fetch = opsgenie_get_json,
                 base_url: str | None = None) -> list[AlertEvent]:
    """Return one ``AlertEvent`` per Opsgenie alert created in the last ``days`` days."""
    if not api_key:
        raise SourceError("set OPSGENIE_API_KEY to an Opsgenie API key with Read access")
    if days <= 0:
        raise SourceError("--days must be positive")
    if region not in API_URLS:
        raise SourceError(f"unknown Opsgenie region {region!r}; use one of {sorted(API_URLS)}")
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    base_url = base_url or API_URLS[region]
    headers = {"Authorization": f"GenieKey {api_key}", "Accept": "application/json"}

    events: list[AlertEvent] = []
    offset = 0
    while True:
        params = [("limit", str(PAGE_SIZE)), ("offset", str(offset)),
                  ("sort", "createdAt"), ("order", "desc")]
        if query:
            params.append(("query", query))
        body = fetch(f"{base_url}/v2/alerts?{urllib.parse.urlencode(params)}", headers)
        page = body.get("data")
        if not isinstance(page, list):
            raise SourceError("unexpected Opsgenie response from /v2/alerts: no 'data' list")
        for index, alert in enumerate(page):
            event = to_event(alert, f"#{offset + index + 1}")
            if event.fired_at < since:
                return sorted(events, key=lambda e: e.fired_at)
            if event.fired_at <= now:
                events.append(event)
        offset += len(page)
        if not page or not (body.get("paging") or {}).get("next"):
            return sorted(events, key=lambda e: e.fired_at)
        if offset + PAGE_SIZE > MAX_OFFSET:
            raise SourceError(f"more than {MAX_OFFSET} alerts in the window; use a smaller --days "
                              "or narrow with --opsgenie-query")
