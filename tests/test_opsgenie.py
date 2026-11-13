import urllib.parse
from datetime import datetime, timezone

import pytest

from alert_noise_auditor import cli, opsgenie
from alert_noise_auditor.loader import load_file
from alert_noise_auditor.opsgenie import SourceError, fetch_alerts, to_event

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def alert(tiny, message, created, status="closed", ack_ms=None, close_ms=None, tags=(),
          priority="P3", acknowledged=None, updated=None):
    body = {"id": f"id-{tiny}", "tinyId": str(tiny), "message": message, "status": status,
            "acknowledged": bool(ack_ms) if acknowledged is None else acknowledged,
            "tags": list(tags), "priority": priority, "createdAt": created,
            "updatedAt": updated or created, "count": 1, "report": {}}
    if ack_ms is not None:
        body["report"]["ackTime"] = ack_ms
    if close_ms is not None:
        body["report"]["closeTime"] = close_ms
    return body


class FakeOpsgenie:
    """Serves alerts newest first by offset, like GET /v2/alerts?sort=createdAt&order=desc."""

    def __init__(self, alerts, page_size=2):
        self.alerts = sorted(alerts, key=lambda a: a["createdAt"], reverse=True)
        self.page_size = page_size
        self.requests = []

    def __call__(self, url, headers):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.requests.append((parsed.netloc, parsed.path, query, headers))
        offset = int(query["offset"][0])
        page = self.alerts[offset:offset + self.page_size]
        paging = {"first": "f", "last": "l"}
        if offset + len(page) < len(self.alerts):
            paging["next"] = f"https://api.opsgenie.com/v2/alerts?offset={offset + len(page)}"
        return {"data": page, "paging": paging, "took": 0.01, "requestId": "r"}


ALERTS = [
    alert(1, "DiskUsageHigh", "2026-11-01T02:00:00.000Z", close_ms=240_000,
          tags=["service:logs", "env:prod"]),
    alert(2, "DiskUsageHigh", "2026-11-01T02:10:00.000Z", close_ms=180_000, tags=["service:logs"]),
    alert(3, "CheckoutErrors", "2026-11-02T09:00:00.000Z", ack_ms=120_000, close_ms=2_400_000,
          tags=["Service=checkout"], priority="P1"),
    alert(4, "DiskUsageHigh", "2026-11-09T23:00:00.000Z", status="open", ack_ms=600_000,
          tags=["service:logs"]),
    alert(5, "OldAlert", "2026-10-01T00:00:00.000Z"),  # before the window: stops paging
    alert(6, "OlderAlert", "2026-09-01T00:00:00.000Z"),
]


def test_fetch_alerts_maps_and_stops_at_window_start():
    fake = FakeOpsgenie(ALERTS)
    events = fetch_alerts("key", days=14, query="teams:sre", now=NOW, fetch=fake)

    assert [(e.name, e.service, e.severity) for e in events] == [
        ("DiskUsageHigh", "logs", "p3"),
        ("DiskUsageHigh", "logs", "p3"),
        ("CheckoutErrors", "checkout", "p1"),
        ("DiskUsageHigh", "logs", "p3"),
    ]
    first, _, checkout, open_one = events
    assert first.resolved_at == datetime(2026, 11, 1, 2, 4, tzinfo=timezone.utc)
    assert first.acknowledged_at is None
    assert checkout.acknowledged_at == datetime(2026, 11, 2, 9, 2, tzinfo=timezone.utc)
    assert checkout.resolved_at == datetime(2026, 11, 2, 9, 40, tzinfo=timezone.utc)
    assert open_one.acknowledged_at is not None and open_one.resolved_at is None

    # Three pages of two; the third holds the out-of-window alert, so no fourth request.
    assert len(fake.requests) == 3
    host, path, query, headers = fake.requests[0]
    assert (host, path) == ("api.opsgenie.com", "/v2/alerts")
    assert query["sort"] == ["createdAt"] and query["order"] == ["desc"]
    assert query["query"] == ["teams:sre"]
    assert [r[2]["offset"] for r in fake.requests] == [["0"], ["2"], ["4"]]
    assert headers["Authorization"] == "GenieKey key"


def test_eu_region_and_last_page():
    fake = FakeOpsgenie(ALERTS[:2], page_size=5)
    events = fetch_alerts("key", days=30, region="eu", now=NOW, fetch=fake)
    assert len(events) == 2
    assert fake.requests[0][0] == "api.eu.opsgenie.com"
    assert "query" not in fake.requests[0][2]


def test_ack_and_close_fallbacks():
    acked = to_event(alert(7, "X", "2026-11-01T00:00:00Z", status="closed", acknowledged=True,
                           updated="2026-11-01T00:30:00Z"), "#1")
    when = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)
    assert acked.acknowledged_at == when and acked.resolved_at == when
    assert acked.service == ""


def test_errors():
    with pytest.raises(SourceError, match="OPSGENIE_API_KEY"):
        fetch_alerts("", now=NOW, fetch=FakeOpsgenie([]))
    with pytest.raises(SourceError, match="region"):
        fetch_alerts("key", region="mars", now=NOW, fetch=FakeOpsgenie([]))
    with pytest.raises(SourceError, match="no 'data' list"):
        fetch_alerts("key", now=NOW, fetch=lambda url, headers: {"message": "nope"})
    with pytest.raises(SourceError, match="missing message"):
        to_event({"tinyId": "9", "createdAt": "2026-11-01T00:00:00Z"}, "#1")


def test_offset_cap_is_reported(monkeypatch):
    monkeypatch.setattr(opsgenie, "MAX_OFFSET", 4)
    recent = [alert(i, "Spam", f"2026-11-09T{i:02d}:00:00Z") for i in range(10)]
    with pytest.raises(SourceError, match="more than 4 alerts"):
        fetch_alerts("key", now=NOW, fetch=FakeOpsgenie(recent, page_size=3))


def test_cli_opsgenie_source_and_save(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_fetch(api_key, days, query, region):
        seen.update(api_key=api_key, days=days, query=query, region=region)
        return fetch_alerts(api_key, days, query, region, now=NOW, fetch=FakeOpsgenie(ALERTS))

    monkeypatch.setenv("OPSGENIE_API_KEY", "key")
    monkeypatch.setattr(cli, "fetch_alerts", fake_fetch)
    saved = tmp_path / "history.csv"
    code = cli.main(["analyze", "--source", "opsgenie", "--opsgenie-query", "teams:sre",
                     "--opsgenie-region", "eu", "--days", "14", "--save", str(saved),
                     "--min-firings", "2"])
    assert code == 0
    assert seen == {"api_key": "key", "days": 14.0, "query": "teams:sre", "region": "eu"}
    out = capsys.readouterr().out
    assert "| DiskUsageHigh [logs] |" in out
    assert "| CheckoutErrors [checkout] |" in out
    assert len(load_file(str(saved))) == 4


def test_cli_opsgenie_errors(monkeypatch, capsys):
    monkeypatch.delenv("OPSGENIE_API_KEY", raising=False)
    assert cli.main(["analyze", "--source", "opsgenie"]) == 2
    assert "opsgenie: set OPSGENIE_API_KEY" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["analyze", "--source", "opsgenie", "examples/alerts.csv"])
