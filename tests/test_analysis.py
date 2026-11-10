from datetime import datetime, timedelta, timezone

from alert_noise_auditor.analysis import Settings, audit, co_firings, count_flaps, filter_recent
from alert_noise_auditor.loader import AlertEvent

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def event(name, minute, duration=5.0, ack=None, service=""):
    fired = T0 + timedelta(minutes=minute)
    return AlertEvent(
        name, fired, service, "warning",
        fired + timedelta(minutes=ack) if ack is not None else None,
        fired + timedelta(minutes=duration) if duration is not None else None)


def by_key(result):
    return {a.key: a for a in result.alerts}


def test_empty_input():
    result = audit([])
    assert result.alerts == [] and result.total_firings == 0


def test_count_flaps_uses_gap_after_resolution():
    events = [event("A", 0, 2), event("A", 10, 2), event("A", 50, 2), event("A", 200, None),
              event("A", 201, 2)]
    # 10 is 8 min after the first resolve (flap); 50 is 38 min after (no);
    # 200 is too late; 201 follows an unresolved firing so it cannot be judged.
    assert count_flaps(events, timedelta(minutes=30)) == 1


def test_flapping_alert_is_tuned_with_for_suggestion():
    events = [event("Flappy", m, 2, ack=1) for m in range(0, 60, 6)]
    stats = by_key(audit(events))["Flappy"]
    assert stats.flaps == 9 and stats.flap_ratio == 0.9
    assert stats.recommendation == "tune"
    assert stats.suggested_for_minutes == 2
    assert "`for: 2m` would have suppressed 10 of 10 firings" in stats.reasons[0]


def test_for_suggestion_is_capped():
    events = [event("Flappy", m * 60, 45, ack=1) for m in range(6)]
    stats = by_key(audit(events))["Flappy"]
    assert stats.suggested_for_minutes == 15
    assert "suppressed 0 of 6" in stats.reasons[0]


def test_never_acknowledged_is_deleted_only_with_enough_firings():
    acked = [event("Real", d * 1440, ack=2) for d in range(4)]
    ignored = [event("Ignored", d * 1440 + 300) for d in range(4)]
    rare = [event("Rare", 100), event("Rare", 5000)]
    alerts = by_key(audit(acked + ignored + rare))
    assert alerts["Ignored"].recommendation == "delete"
    assert alerts["Ignored"].reasons == ["never acknowledged in 4 firings"]
    assert alerts["Rare"].recommendation == "keep"  # only 2 firings: not enough evidence
    assert alerts["Real"].recommendation == "keep" and alerts["Real"].ack_rate == 1.0
    assert alerts["Ignored"].noise_score > alerts["Real"].noise_score


def test_rarely_acknowledged_is_tuned():
    events = [event("Meh", d * 1440, ack=3 if d == 0 else None) for d in range(8)]
    stats = by_key(audit(events))["Meh"]
    assert stats.recommendation == "tune"
    assert stats.reasons[0].startswith("acknowledged only 1 of 8 times")


def test_no_ack_data_disables_never_acknowledged_and_rescales_score():
    events = [event("A", d * 1440) for d in range(5)]
    result = audit(events)
    stats = result.alerts[0]
    assert result.has_ack_data is False
    assert stats.ack_rate is None and stats.recommendation == "keep"
    # only volume contributes: 25 * (1.25 per day / 5) out of the 65 available points
    assert stats.noise_score == round(100 * 25 * 0.25 / 65)


def test_duplicates_report_the_less_acknowledged_alert():
    events = []
    for d in range(5):
        events.append(event("Latency", d * 1440, ack=2, service="api"))
        events.append(event("Saturation", d * 1440 + 1, service="api"))
    events += [event("Other", d * 1440 + 600, ack=1) for d in range(5)]
    alerts = by_key(audit(events))
    assert alerts["Saturation [api]"].duplicate_of == "Latency [api]"
    assert alerts["Saturation [api]"].duplicate_overlap == 1.0
    assert alerts["Latency [api]"].duplicate_of is None
    assert alerts["Other"].duplicate_of is None
    # never acknowledged wins over duplicate, but both reasons are listed
    assert alerts["Saturation [api]"].recommendation == "delete"
    assert len(alerts["Saturation [api]"].reasons) == 2


def test_partial_overlap_is_not_a_duplicate():
    a = [event("A", d * 1440, ack=1) for d in range(10)]
    b = [event("B", d * 1440 + (1 if d < 5 else 300), ack=1) for d in range(10)]
    assert co_firings(a, b, 120) == 5
    assert all(s.duplicate_of is None for s in audit(a + b).alerts)


def test_duplicate_window_setting():
    a = [event("A", d * 1440, ack=1) for d in range(4)]
    b = [event("B", d * 1440 + 4, ack=1) for d in range(4)]
    assert all(s.duplicate_of is None for s in audit(a + b).alerts)
    wide = audit(a + b, Settings(duplicate_window_seconds=300))
    assert by_key(wide)["B"].duplicate_of == "A"


def test_same_name_in_different_services_is_separate():
    events = [event("Down", 0, service="a"), event("Down", 10, service="b")]
    assert sorted(by_key(audit(events))) == ["Down [a]", "Down [b]"]


def test_filter_recent_counts_back_from_newest_event():
    events = [event("A", d * 1440) for d in range(10)]
    assert len(filter_recent(events, 3)) == 4
    assert filter_recent(events, None) == events
    assert filter_recent([], 3) == []


def test_alerts_sorted_by_noise_score():
    events = [event("Quiet", d * 1440, ack=1) for d in range(3)]
    events += [event("Loud", m, 2) for m in range(0, 300, 5)]
    result = audit(events)
    assert [a.key for a in result.alerts] == ["Loud", "Quiet"]
    assert 0 <= result.alerts[1].noise_score < result.alerts[0].noise_score <= 100
