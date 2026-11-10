import json
from datetime import datetime, timezone

import pytest

from alert_noise_auditor.loader import InputError, load_text, parse_time

UTC = timezone.utc


def test_parse_time_variants():
    expected = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    assert parse_time("2026-10-01T10:00:00Z") == expected
    assert parse_time("2026-10-01 10:00:00") == expected  # naive means UTC
    assert parse_time("2026-10-01T12:00:00+02:00") == expected
    assert parse_time("2026-10-01T10:00:00.123456789Z") == expected.replace(microsecond=123456)
    assert parse_time("2026-10-01T10:00:00.5Z") == expected.replace(microsecond=500000)
    assert parse_time("") is None and parse_time(None) is None
    assert parse_time("0001-01-01T00:00:00Z") is None  # Alertmanager "not resolved"
    with pytest.raises(InputError):
        parse_time("yesterday")


def test_csv_with_aliases_blank_lines_and_sorting():
    text = (
        "Alert,Job,Severity,triggered_at,acked_at,closed_at\n"
        "B,api,CRITICAL,2026-10-02T00:00:00Z,,\n"
        "\n"
        "A,api,warning,2026-10-01T00:00:00Z,2026-10-01T00:02:00Z,2026-10-01T00:10:00Z\n"
    )
    events = load_text(text)
    assert [e.name for e in events] == ["A", "B"]
    assert events[0].key == "A [api]"
    assert events[0].duration_seconds == 600
    assert events[0].acknowledged_at is not None
    assert events[1].severity == "critical"
    assert events[1].resolved_at is None and events[1].duration_seconds is None


def test_csv_errors_name_the_line():
    with pytest.raises(InputError, match="line 2: missing fired_at"):
        load_text("name,fired_at\nA,\n")
    with pytest.raises(InputError, match="line 3: missing alert name"):
        load_text("name,fired_at\nA,2026-10-01T00:00:00Z\n,2026-10-01T00:00:00Z\n")
    with pytest.raises(InputError, match="line 2: cannot parse timestamp"):
        load_text("name,fired_at\nA,not-a-time\n")


def test_flat_json():
    events = load_text(json.dumps([{"name": "A", "fired_at": "2026-10-01T00:00:00Z"}]))
    assert events[0].key == "A"


def test_alertmanager_json_array_and_webhook_payload():
    alerts = [
        {"labels": {"alertname": "Down", "job": "node", "severity": "critical"},
         "startsAt": "2026-10-01T00:00:00.000Z", "endsAt": "2026-10-01T00:05:00.000Z"},
        {"labels": {"alertname": "Down", "service": "edge"},
         "startsAt": "2026-10-01T01:00:00Z", "endsAt": "0001-01-01T00:00:00Z"},
        {"labels": {"alertname": "Slow"}, "status": {"state": "active", "silencedBy": []},
         "startsAt": "2026-10-01T02:00:00Z", "endsAt": "2026-10-01T02:04:00Z"},
    ]
    for text in (json.dumps(alerts), json.dumps({"version": "4", "alerts": alerts})):
        events = load_text(text)
        assert [e.key for e in events] == ["Down [node]", "Down [edge]", "Slow"]
        assert events[0].duration_seconds == 300
        assert events[1].resolved_at is None
        assert events[2].resolved_at is None  # still firing: endsAt is only a timeout


def test_bad_json_shapes():
    with pytest.raises(InputError, match="invalid JSON"):
        load_text("[{")
    with pytest.raises(InputError, match="must be an array"):
        load_text('{"foo": 1}')
    with pytest.raises(InputError, match="record 1: expected an object"):
        load_text("[1]")
