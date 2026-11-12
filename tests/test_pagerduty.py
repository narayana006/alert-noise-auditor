import urllib.parse
from datetime import datetime, timezone

import pytest

from alert_noise_auditor import cli, pagerduty
from alert_noise_auditor.loader import load_file
from alert_noise_auditor.pagerduty import SourceError, fetch_incidents

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def incident(number, title, created, status="resolved", resolved=None, service="Payments API",
             urgency="high"):
    body = {"id": f"P{number:04d}", "incident_number": number, "title": title,
            "created_at": created, "status": status, "urgency": urgency,
            "service": {"id": "PSVC1", "type": "service_reference", "summary": service}}
    if resolved:
        body["resolved_at"] = resolved
    return body


def log(kind, incident_id, created):
    return {"id": f"L{incident_id}{kind[:3]}", "type": kind, "created_at": created,
            "incident": {"id": incident_id, "type": "incident_reference"}}


class FakePagerDuty:
    """Serves canned pages by path and offset, and records every request."""

    def __init__(self, incidents, log_entries, page_size=2):
        self.pages = {"/incidents": ("incidents", incidents),
                      "/log_entries": ("log_entries", log_entries)}
        self.page_size = page_size
        self.requests = []

    def __call__(self, url, headers):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.requests.append((parsed.path, query, headers))
        key, items = self.pages[parsed.path]
        offset = int(query["offset"][0])
        page = items[offset:offset + self.page_size]
        return {key: page, "offset": offset, "limit": 100,
                "more": offset + len(page) < len(items)}


INCIDENTS = [
    incident(1, "DiskUsageHigh", "2026-11-01T02:00:00Z", resolved="2026-11-01T02:04:00Z"),
    incident(2, "DiskUsageHigh", "2026-11-01T02:10:00Z"),  # resolved_at only in the log
    incident(3, "CheckoutErrors", "2026-11-02T09:00:00Z", resolved="2026-11-02T09:40:00Z",
             service="Checkout", urgency="High"),
    incident(4, "DiskUsageHigh", "2026-11-09T23:00:00Z", status="acknowledged"),
]
LOG_ENTRIES = [
    log("trigger_log_entry", "P0001", "2026-11-01T02:00:00Z"),
    log("resolve_log_entry", "P0002", "2026-11-01T02:13:00Z"),
    log("acknowledge_log_entry", "P0003", "2026-11-02T09:05:00Z"),
    log("acknowledge_log_entry", "P0003", "2026-11-02T09:02:00Z"),  # earliest wins
    log("acknowledge_log_entry", "P0004", "2026-11-09T23:10:00Z"),
    log("acknowledge_log_entry", "P9999", "2026-11-03T00:00:00Z"),  # other service, ignored
]


def test_fetch_incidents_maps_and_paginates():
    fake = FakePagerDuty(INCIDENTS, LOG_ENTRIES)
    events = fetch_incidents("tok", days=14, service_ids=("PSVC1",), now=NOW, fetch=fake)

    assert [(e.name, e.service, e.severity) for e in events] == [
        ("DiskUsageHigh", "Payments API", "high"),
        ("DiskUsageHigh", "Payments API", "high"),
        ("CheckoutErrors", "Checkout", "high"),
        ("DiskUsageHigh", "Payments API", "high"),
    ]
    first, second, checkout, open_one = events
    assert first.resolved_at == datetime(2026, 11, 1, 2, 4, tzinfo=timezone.utc)
    assert first.acknowledged_at is None
    assert second.resolved_at == datetime(2026, 11, 1, 2, 13, tzinfo=timezone.utc)
    assert checkout.acknowledged_at == datetime(2026, 11, 2, 9, 2, tzinfo=timezone.utc)
    assert open_one.acknowledged_at is not None and open_one.resolved_at is None

    paths = [path for path, _, _ in fake.requests]
    assert paths == ["/incidents", "/incidents", "/log_entries", "/log_entries", "/log_entries"]
    _, query, headers = fake.requests[0]
    assert query["since"] == ["2026-10-27T12:00:00Z"] and query["until"] == ["2026-11-10T12:00:00Z"]
    assert query["service_ids[]"] == ["PSVC1"]
    assert set(query["statuses[]"]) == {"triggered", "acknowledged", "resolved"}
    assert headers["Authorization"] == "Token token=tok"
    assert "version=2" in headers["Accept"]
    assert fake.requests[2][1]["is_overview"] == ["true"]


def test_no_incidents_skips_log_entries():
    fake = FakePagerDuty([], LOG_ENTRIES)
    assert fetch_incidents("tok", now=NOW, fetch=fake) == []
    assert [path for path, _, _ in fake.requests] == ["/incidents"]


def test_missing_token_and_bad_response():
    with pytest.raises(SourceError, match="PAGERDUTY_API_TOKEN"):
        fetch_incidents("", now=NOW, fetch=FakePagerDuty([], []))
    with pytest.raises(SourceError, match="no 'incidents' list"):
        fetch_incidents("tok", now=NOW, fetch=lambda url, headers: {"error": "nope"})


def test_offset_cap_is_reported(monkeypatch):
    monkeypatch.setattr(pagerduty, "MAX_OFFSET", 4)
    fake = FakePagerDuty(INCIDENTS * 3, [], page_size=3)
    with pytest.raises(SourceError, match="more than 4 records"):
        fetch_incidents("tok", now=NOW, fetch=fake)


def test_cli_pagerduty_source_and_save(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_fetch(token, days, service_ids):
        seen.update(token=token, days=days, service_ids=service_ids)
        return fetch_incidents(token, days, service_ids, now=NOW,
                               fetch=FakePagerDuty(INCIDENTS, LOG_ENTRIES))

    monkeypatch.setenv("PAGERDUTY_API_TOKEN", "tok")
    monkeypatch.setattr(cli, "fetch_incidents", fake_fetch)
    saved = tmp_path / "history.csv"
    code = cli.main(["analyze", "--source", "pagerduty", "--pagerduty-service", "PSVC1",
                     "--save", str(saved), "--min-firings", "2"])
    assert code == 0
    assert seen == {"token": "tok", "days": 30.0, "service_ids": ("PSVC1",)}
    out = capsys.readouterr().out
    assert "| DiskUsageHigh [Payments API] |" in out
    assert "| CheckoutErrors [Checkout] |" in out

    reloaded = load_file(str(saved))
    assert len(reloaded) == 4
    assert reloaded[2].acknowledged_at == datetime(2026, 11, 2, 9, 2, tzinfo=timezone.utc)


def test_cli_pagerduty_errors(monkeypatch, capsys):
    monkeypatch.delenv("PAGERDUTY_API_TOKEN", raising=False)
    assert cli.main(["analyze", "--source", "pagerduty"]) == 2
    assert "pagerduty: set PAGERDUTY_API_TOKEN" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["analyze", "--source", "pagerduty", "examples/alerts.csv"])
    with pytest.raises(SystemExit):
        cli.main(["analyze"])
