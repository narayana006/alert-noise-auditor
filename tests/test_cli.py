import io
import json
from pathlib import Path

from alert_noise_auditor.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CSV = str(EXAMPLES / "alerts.csv")
ALERTMANAGER = str(EXAMPLES / "alertmanager.json")


def test_csv_example_report(capsys):
    assert main(["analyze", CSV]) == 0
    out = capsys.readouterr().out
    assert "**Recommendations:** delete 1, tune 3, keep 3." in out
    assert "| DiskUsageHigh [logs] | 57 | TUNE |" in out
    assert "- never acknowledged in 24 firings" in out
    assert "fires together with APILatencyHigh [api] (100% overlap)" in out


def test_alertmanager_example_report(capsys):
    assert main(["analyze", ALERTMANAGER]) == 0
    out = capsys.readouterr().out
    assert "no acknowledgement timestamps" in out
    assert "| QueueDepthHigh [orders-consumer] | 29 | TUNE | 4 | n/a |" in out


def test_json_output_and_days(capsys):
    assert main(["analyze", CSV, "--format", "json", "--days", "7"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["days"] <= 7
    assert data["total_firings"] == sum(a["firings"] for a in data["alerts"])
    assert {"key", "noise_score", "recommendation", "reasons"} <= set(data["alerts"][0])


def test_top_limits_rows(capsys):
    assert main(["analyze", CSV, "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert "Showing the 2 noisiest of 7 alerts." in out
    assert "CronJobSlow" not in out


def test_fail_on_delete(capsys):
    assert main(["analyze", CSV, "--fail-on-delete"]) == 1
    assert main(["analyze", ALERTMANAGER, "--fail-on-delete"]) == 0
    capsys.readouterr()


def test_stdin_and_empty_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("name,fired_at\n"))
    assert main(["analyze", "-"]) == 0
    assert "No alert events in the input." in capsys.readouterr().out


def test_errors_exit_2(tmp_path, capsys):
    assert main(["analyze", str(tmp_path / "missing.csv")]) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("name,fired_at\nA,nope\n")
    assert main(["analyze", str(bad)]) == 2
    assert "line 2: cannot parse timestamp 'nope'" in capsys.readouterr().err
