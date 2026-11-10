# alert-noise-auditor

Reads an export of your alert history and tells you which alerts are noise: the ones that flap, the ones nobody ever acknowledges, and the ones that duplicate another alert. Each alert gets a noise score and a keep / tune / delete recommendation.

**Status:** v0.1.0, usable from the command line with file input. Working today: CSV, JSON and Alertmanager-style JSON input; flapping, never-acknowledged and duplicate detection; per-alert noise score; `for:` duration suggestions for flapping alerts; Markdown and JSON reports. Not built yet: pulling history directly from PagerDuty, Opsgenie or a live Alertmanager, and threshold suggestions.

## Install

Python 3.10+, no runtime dependencies.

```bash
git clone https://github.com/narayana006/alert-noise-auditor.git
cd alert-noise-auditor
pip install .
```

## Try it

`examples/alerts.csv` is a synthetic 30-day history for seven alerts.

```bash
alert-noise-auditor analyze examples/alerts.csv
```

```markdown
# Alert noise audit

81 firings of 7 alerts between 2026-10-01 and 2026-10-30 (29.3 days).

**Recommendations:** delete 1, tune 3, keep 3. Alerts marked delete or tune produced 68 of 81 firings (84%).

| Alert | Noise score | Recommendation | Firings | Ack rate | Flap rate | Median duration | Duplicate of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DiskUsageHigh [logs] | 57 | TUNE | 28 | 7% | 79% | 2m | - |
| APISaturationHigh [api] | 46 | TUNE | 7 | 14% | 0% | 13m | APILatencyHigh [api] |
| CronJobSlow [batch] | 39 | DELETE | 24 | 0% | 0% | 20.5m | - |
| CertExpiringSoon [edge] | 33 | TUNE | 9 | 11% | 0% | 1410m | - |
| APILatencyHigh [api] | 1 | KEEP | 7 | 100% | 0% | 14m | - |
| HighErrorRate [checkout] | 1 | KEEP | 4 | 100% | 0% | 40m | - |
| DatabaseReplicationLag [db] | 0 | KEEP | 2 | 100% | 0% | 18m | - |

## Delete

- **CronJobSlow [batch]** (score 39)
  - never acknowledged in 24 firings

## Tune

- **DiskUsageHigh [logs]** (score 57)
  - acknowledged only 2 of 28 times; raise the threshold or lower the severity
  - flapping: 22 of 28 firings re-fired within 30 min of resolving; `for: 3m` would have suppressed 23 of 28 firings
- **APISaturationHigh [api]** (score 46)
  - acknowledged only 1 of 7 times; raise the threshold or lower the severity
  - fires together with APILatencyHigh [api] (100% overlap); merge the two or inhibit this one
- **CertExpiringSoon [edge]** (score 33)
  - acknowledged only 1 of 9 times; raise the threshold or lower the severity
```

## Input

One record is one firing of one alert. Export that from whatever you use and save it as CSV or JSON.

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Alert name. Also accepted: `alertname`, `alert_name`, `alert`, `title` |
| `fired_at` | yes | When the alert fired. Also accepted: `startsAt`, `started_at`, `triggered_at`, `created_at` |
| `resolved_at` | no | When it resolved; empty if it never did. Also accepted: `endsAt`, `ended_at`, `closed_at` |
| `acknowledged_at` | no | When a human acknowledged it; empty if nobody did. Also accepted: `acked_at`, `ack_at` |
| `service` | no | Alerts with the same name in different services are analysed separately. Also accepted: `job`, `component` |
| `severity` | no | Carried through to the JSON report. Also accepted: `priority`, `urgency` |

Timestamps are ISO 8601 (`2026-10-01T02:13:00Z`, `2026-10-01 02:13:00+02:00`). A timestamp without an offset is read as UTC. Column names are case-insensitive and extra columns are ignored.

CSV needs a header row:

```csv
name,service,severity,fired_at,acknowledged_at,resolved_at
CronJobSlow,batch,warning,2026-10-01T02:02:00Z,,2026-10-01T02:38:00Z
```

JSON is an array of objects with the same fields.

### Alertmanager-style JSON

An array of alerts in the shape Alertmanager uses (`GET /api/v2/alerts`), or a webhook payload with an `alerts` array, is also accepted. `labels.alertname` becomes the name, `labels.service` (or `labels.job`) the service, and `startsAt` / `endsAt` the fired and resolved times. An alert whose status is firing/active, or whose `endsAt` is the zero time, counts as unresolved. One record from `examples/alertmanager.json`:

```json
{
  "labels": {
    "alertname": "KafkaConsumerLag",
    "job": "orders-consumer",
    "severity": "critical",
    "instance": "orders-consumer-0"
  },
  "annotations": {
    "summary": "KafkaConsumerLag on orders-consumer-0"
  },
  "startsAt": "2026-11-01T10:30:00.000Z",
  "endsAt": "2026-11-01T10:52:00.000Z",
  "generatorURL": "http://prometheus.example.internal/graph",
  "fingerprint": "842e7fc229540a6e"
}
```

Alertmanager does not record acknowledgements, so for this input the never-acknowledged check is skipped and the report says so:

```bash
alert-noise-auditor analyze examples/alertmanager.json
```

```markdown
| Alert | Noise score | Recommendation | Firings | Ack rate | Flap rate | Median duration | Duplicate of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HighMemoryUsage [node] | 40 | TUNE | 6 | n/a | 83% | 2m | - |
| QueueDepthHigh [orders-consumer] | 29 | TUNE | 4 | n/a | 0% | 20m | KafkaConsumerLag [orders-consumer] |
| KafkaConsumerLag [orders-consumer] | 6 | KEEP | 4 | n/a | 0% | 22m | - |
| InstanceDown [blackbox] | 1 | KEEP | 1 | n/a | 0% | 41m | - |

## Tune

- **HighMemoryUsage [node]** (score 40)
  - flapping: 5 of 6 firings re-fired within 30 min of resolving; `for: 2m` would have suppressed 6 of 6 firings
- **QueueDepthHigh [orders-consumer]** (score 29)
  - fires together with KafkaConsumerLag [orders-consumer] (100% overlap); merge the two or inhibit this one
```

Note that Alertmanager's API only returns alerts that are currently active or recently resolved. To audit a longer period you need to collect its webhook notifications, or export the history from your paging tool into the CSV format above.

## Options

```text
alert-noise-auditor analyze [--input-format {auto,csv,json}] [--format {markdown,json}]
                            [--days N] [--top N] [--flap-gap MIN] [--duplicate-window SEC]
                            [--min-firings N] [--fail-on-delete] FILE
```

- `FILE`: the export, or `-` for stdin. The format comes from the extension, or from the content when there is no `.csv` / `.json` extension.
- `--days N`: use only the last N days of the file, counted back from the newest event in it (not from today).
- `--format json`: the full per-alert statistics as JSON, for dashboards or further scripting.
- `--top N`: show only the N noisiest alerts in the Markdown report.
- `--flap-gap MIN` (default 30), `--duplicate-window SEC` (default 120), `--min-firings N` (default 3): detection settings, explained below.
- `--fail-on-delete`: exit with status 1 when any alert is recommended for deletion.

Exit status is 0 on success and 2 when the input cannot be read or parsed (the message names the offending line or record).

## How the analysis works

- **Flapping.** A firing is a flap when it starts within `--flap-gap` minutes after the previous firing of the same alert resolved. An alert is flapping when it has at least 3 flaps and they make up at least 30% of its firings. For those, the tool suggests a `for:` duration: the 75th percentile of the alert's resolved durations, rounded up to a whole minute and capped at 15 minutes, and reports how many past firings were no longer than that.
- **Never acknowledged.** An alert with at least `--min-firings` firings and no acknowledgement at all. Alerts acknowledged 25% of the time or less are reported as rarely acknowledged.
- **Duplicates.** Two alerts are duplicates when at least 80% of the rarer one's firings start within `--duplicate-window` seconds of a firing of the other (both need `--min-firings` firings). The one acknowledged less often is reported as the duplicate.
- **Noise score (0 to 100).** 35 x share of firings not acknowledged + 25 x share of firings that are flaps + 25 x volume (firings per day, maxing out at 5 per day) + 15 x duplicate overlap. Without acknowledgement data the other three parts are rescaled to 0-100.
- **Recommendation.** `DELETE` for never-acknowledged alerts; `TUNE` for flapping, duplicate or rarely acknowledged alerts; otherwise `KEEP`. The score ranks alerts; the recommendation comes from these rules, not from a score cut-off.

## Features

- [x] Import from CSV, JSON and Alertmanager-style JSON files
- [ ] Import directly from PagerDuty, Opsgenie and a live Alertmanager
- [x] Detect flapping, never-acknowledged and duplicate alerts
- [x] Per-alert noise score
- [x] Suggested `for:` duration for flapping alerts
- [ ] Suggested threshold changes
- [x] Report with delete / tune / keep recommendations (Markdown or JSON)

## Limitations

- File input only. There is no `--source pagerduty` yet; you have to produce the export yourself.
- "Acknowledged" is the only signal of human action. An alert that people act on without acknowledging will look ignored, and one that is acknowledged by reflex will look useful.
- The tool sees only firing history, not metric values, so it cannot suggest thresholds. The `for:` suggestion is based purely on how long past firings lasted and also delays real alerts by that amount.
- Duplicate detection compares firing start times pairwise. It finds alerts that fire together, not alerts that are merely similar in name or expression, and it does not group more than two.
- Alerts are identified by name plus service. Other labels (instance, cluster) are ignored, so the same alert on many instances is treated as one alert.
- Recommendations are heuristics over a sample of history. Review them before deleting anything, especially alerts for rare but severe failures.

## Contributing

Bug reports, sample exports (anonymised) and pull requests are welcome. Importers for specific tools are the most useful next step.

```bash
pip install -e ".[dev]"
pytest
```

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
