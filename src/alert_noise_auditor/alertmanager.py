"""Read the alerts currently held by a live Alertmanager as ``AlertEvent`` records.

Only one read-only request is made: ``GET <url>/api/v2/alerts``. Alertmanager
keeps no history, so this is a snapshot of what is firing (or silenced or
inhibited) right now, not a record of past firings. Every returned alert is
reported as unresolved. Mapping of labels follows the Alertmanager-style JSON
input: ``alertname`` is the name, ``service`` (or ``job``) the service.
"""

from __future__ import annotations

import functools
import urllib.parse
from datetime import datetime, timedelta, timezone

from .loader import AlertEvent, _from_alertmanager
from .pagerduty import Fetch, SourceError, http_get_json

ALERTMANAGER_HINTS = {401: " (check ALERTMANAGER_TOKEN)",
                      403: " (the token or proxy forbids reading alerts)",
                      404: " (is --alertmanager-url the Alertmanager root, without /api/v2?)"}

alertmanager_get_json = functools.partial(http_get_json, label="Alertmanager",
                                          hints=ALERTMANAGER_HINTS)


def fetch_alerts(url: str, token: str = "", days: float = 30.0, include_silenced: bool = True,
                 now: datetime | None = None, fetch: Fetch = alertmanager_get_json
                 ) -> list[AlertEvent]:
    """Return one unresolved ``AlertEvent`` per alert Alertmanager currently holds.

    Alerts that started more than ``days`` days ago are left out.
    """
    if not url:
        raise SourceError("set --alertmanager-url to the Alertmanager base URL")
    if days <= 0:
        raise SourceError("--days must be positive")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SourceError(f"--alertmanager-url must start with http:// or https://, got {url!r}")
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    flag = "true" if include_silenced else "false"
    query = urllib.parse.urlencode({"active": "true", "silenced": flag, "inhibited": flag})
    endpoint = f"{url.rstrip('/')}/api/v2/alerts?{query}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = fetch(endpoint, headers)
    if not isinstance(body, list):
        raise SourceError("unexpected Alertmanager response from /api/v2/alerts: not a list")
    events = []
    for index, alert in enumerate(body):
        if not isinstance(alert, dict):
            raise SourceError(f"alert #{index + 1}: expected an object")
        event = _from_alertmanager(alert, f"alert #{index + 1}")
        if event.fired_at < since:
            continue
        # Everything the API returns is still present, so none of it has resolved.
        events.append(AlertEvent(event.name, event.fired_at, event.service, event.severity,
                                 event.acknowledged_at, None))
    return sorted(events, key=lambda e: e.fired_at)
