import urllib.parse
from datetime import datetime, timezone

import pytest

from alert_noise_auditor import cli
from alert_noise_auditor.alertmanager import SourceError, fetch_alerts
from alert_noise_auditor.loader import load_file

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def am_alert(name, starts, ends="2026-11-10T12:05:00.000Z", state="active", **labels):
    return {"labels": {"alertname": name, **labels}, "startsAt": starts, "endsAt": ends,
            "status": {"state": state, "silencedBy": [], "inhibitedBy": []},
            "fingerprint": "abc"}


BODY = [
    am_alert("HighMemoryUsage", "2026-11-10T11:00:00.000Z", job="node", severity="warning"),
    am_alert("DiskFull", "2026-11-09T08:00:00.000Z", state="suppressed", service="logs"),
    am_alert("Ancient", "2026-09-01T00:00:00.000Z"),
]


class FakeAlertmanager:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, headers))
        return self.body


def test_snapshot_is_mapped_and_unresolved():
    fake = FakeAlertmanager(BODY)
    events = fetch_alerts("http://am:9093/", token="tok", days=14, now=NOW, fetch=fake)

    assert [(e.name, e.service, e.severity) for e in events] == [
        ("DiskFull", "logs", ""), ("HighMemoryUsage", "node", "warning")]
    assert all(e.resolved_at is None and e.acknowledged_at is None for e in events)
    url, headers = fake.requests[0]
    parsed = urllib.parse.urlparse(url)
    assert (parsed.netloc, parsed.path) == ("am:9093", "/api/v2/alerts")
    assert urllib.parse.parse_qs(parsed.query)["silenced"] == ["true"]
    assert headers["Authorization"] == "Bearer tok"


def test_exclude_silenced_and_no_token():
    fake = FakeAlertmanager([])
    assert fetch_alerts("https://am.example", include_silenced=False, now=NOW, fetch=fake) == []
    url, headers = fake.requests[0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["silenced"] == ["false"] and query["inhibited"] == ["false"]
    assert "Authorization" not in headers


def test_errors():
    fake = FakeAlertmanager([])
    with pytest.raises(SourceError, match="alertmanager-url"):
        fetch_alerts("", now=NOW, fetch=fake)
    with pytest.raises(SourceError, match="http"):
        fetch_alerts("am:9093", now=NOW, fetch=fake)
    with pytest.raises(SourceError, match="positive"):
        fetch_alerts("http://am", days=0, now=NOW, fetch=fake)
    with pytest.raises(SourceError, match="not a list"):
        fetch_alerts("http://am", now=NOW, fetch=FakeAlertmanager({"error": "x"}))
    with pytest.raises(SourceError, match="expected an object"):
        fetch_alerts("http://am", now=NOW, fetch=FakeAlertmanager(["x"]))


def test_cli_alertmanager_source_and_save(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_fetch(url, token, days):
        seen.update(url=url, token=token, days=days)
        return fetch_alerts(url, token, days, now=NOW, fetch=FakeAlertmanager(BODY))

    monkeypatch.setenv("ALERTMANAGER_TOKEN", "tok")
    monkeypatch.setattr(cli, "fetch_alertmanager", fake_fetch)
    saved = tmp_path / "snapshot.csv"
    code = cli.main(["analyze", "--source", "alertmanager", "--alertmanager-url",
                     "http://am:9093", "--days", "14", "--save", str(saved)])
    assert code == 0
    assert seen == {"url": "http://am:9093", "token": "tok", "days": 14.0}
    assert "| HighMemoryUsage [node] |" in capsys.readouterr().out
    assert len(load_file(str(saved))) == 2


def test_cli_alertmanager_requires_url(monkeypatch, capsys):
    assert cli.main(["analyze", "--source", "alertmanager"]) == 2
    assert "alertmanager-url" in capsys.readouterr().err
