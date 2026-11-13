"""Fetch incident history from the PagerDuty REST API (v2) as ``AlertEvent`` records.

Only read-only ``GET`` requests are made:

* ``GET /incidents`` for every incident created in the window (title, service,
  urgency, creation and resolve times)
* ``GET /log_entries?is_overview=true`` for acknowledgement times, which the
  incident object does not keep once the incident is resolved

One incident becomes one firing. The incident title is the alert name and the
PagerDuty service name is the service.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from .loader import AlertEvent, InputError, parse_time

API_URL = "https://api.pagerduty.com"
PAGE_SIZE = 100
# PagerDuty's offset pagination stops at offset + limit = 10000.
MAX_OFFSET = 10_000

# (url, headers) -> decoded JSON body. Replaced by a fake in tests.
Fetch = Callable[[str, dict], dict]


class SourceError(InputError):
    """The remote source could not be read."""


PAGERDUTY_HINTS = {401: " (check PAGERDUTY_API_TOKEN)", 403: " (the token lacks read access)",
                   429: " (rate limited; try again shortly or narrow --days)"}


def http_get_json(url: str, headers: dict, timeout: float = 30.0, label: str = "PagerDuty",
                  hints: dict[int, str] | None = None) -> dict:
    """GET ``url`` and decode the JSON body, turning failures into ``SourceError``."""
    hints = PAGERDUTY_HINTS if hints is None else hints
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        hint = hints.get(error.code, "")
        raise SourceError(f"{label} returned HTTP {error.code} for {url.split('?')[0]}{hint}") \
            from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise SourceError(f"cannot reach {label}: {error}") from error
    except json.JSONDecodeError as error:
        raise SourceError(f"{label} returned invalid JSON: {error}") from error


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paginate(fetch: Fetch, base_url: str, path: str, key: str,
              params: list[tuple[str, str]], headers: dict) -> Iterator[dict]:
    offset = 0
    while True:
        query = urllib.parse.urlencode(params + [("limit", str(PAGE_SIZE)), ("offset", str(offset))])
        body = fetch(f"{base_url}{path}?{query}", headers)
        items = body.get(key)
        if not isinstance(items, list):
            raise SourceError(f"unexpected PagerDuty response from {path}: no {key!r} list")
        yield from items
        offset += len(items)
        if not body.get("more") or not items:
            return
        if offset + PAGE_SIZE > MAX_OFFSET:
            raise SourceError(f"more than {MAX_OFFSET} records in {path}; use a smaller --days "
                              "or filter with --pagerduty-service")


def fetch_incidents(token: str, days: float = 30.0, service_ids: tuple[str, ...] = (),
                    now: datetime | None = None, fetch: Fetch = http_get_json,
                    base_url: str = API_URL) -> list[AlertEvent]:
    """Return one ``AlertEvent`` per PagerDuty incident created in the last ``days`` days."""
    if not token:
        raise SourceError("set PAGERDUTY_API_TOKEN to a read-only PagerDuty REST API key")
    if days <= 0:
        raise SourceError("--days must be positive")
    now = now or datetime.now(timezone.utc)
    since, until = _iso(now - timedelta(days=days)), _iso(now)
    headers = {"Authorization": f"Token token={token}",
               "Accept": "application/vnd.pagerduty+json;version=2"}

    params = [("since", since), ("until", until), ("time_zone", "UTC")]
    params += [("service_ids[]", sid) for sid in service_ids]
    incidents = list(_paginate(fetch, base_url, "/incidents", "incidents",
                               params + [("statuses[]", s) for s in
                                         ("triggered", "acknowledged", "resolved")], headers))

    # The window ends now, so every ack/resolve of these incidents falls inside it.
    acked: dict[str, datetime] = {}
    resolved: dict[str, datetime] = {}
    log_params = [("since", since), ("until", until), ("time_zone", "UTC"), ("is_overview", "true")]
    wanted = {inc.get("id") for inc in incidents}
    if incidents:
        for entry in _paginate(fetch, base_url, "/log_entries", "log_entries", log_params, headers):
            incident_id = (entry.get("incident") or {}).get("id")
            if incident_id not in wanted:
                continue
            target = {"acknowledge_log_entry": acked,
                      "resolve_log_entry": resolved}.get(entry.get("type"))
            when = parse_time(entry.get("created_at"))
            if target is not None and when is not None:
                target[incident_id] = min(when, target.get(incident_id, when))

    events = []
    for index, incident in enumerate(incidents):
        incident_id = incident.get("id")
        name = str(incident.get("title") or incident.get("summary") or "").strip()
        fired_at = parse_time(incident.get("created_at"))
        if not name or fired_at is None:
            raise SourceError(f"incident {incident_id or index + 1}: missing title or created_at")
        resolved_at = None
        if incident.get("status") == "resolved":
            resolved_at = parse_time(incident.get("resolved_at")) or resolved.get(incident_id)
        events.append(AlertEvent(
            name=name,
            fired_at=fired_at,
            service=str((incident.get("service") or {}).get("summary") or "").strip(),
            severity=str(incident.get("urgency") or "").strip().lower(),
            acknowledged_at=acked.get(incident_id),
            resolved_at=resolved_at,
        ))
    return sorted(events, key=lambda e: e.fired_at)
