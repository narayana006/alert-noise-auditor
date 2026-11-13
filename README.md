# alert-noise-auditor

Reads an export of your alert history and tells you which alerts are noise: the ones that flap, the ones nobody ever acknowledges, and the ones that duplicate another alert. Each alert gets a noise score and a keep / tune / delete recommendation.

**Status:** v0.1.0, usable from the command line. Working today: CSV, JSON and Alertmanager-style JSON input; reading history straight from PagerDuty or Opsgenie; flapping, never-acknowledged and duplicate detection; per-alert noise score; `for:` duration suggestions for flapping alerts; Markdown and JSON reports. Not built yet: pulling history directly from a live Alertmanager, and threshold suggestions.

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

### Straight from PagerDuty

With a PagerDuty REST API key in `PAGERDUTY_API_TOKEN` (a read-only key is enough), the tool reads the incident history itself instead of a file:

```bash
export PAGERDUTY_API_TOKEN=...   # read-only REST API key
alert-noise-auditor analyze --source pagerduty --days 30 --save pd-history.csv
```

- It sends only `GET` requests: `/incidents` for every incident created in the last `--days` days (default 30, counted back from now), and `/log_entries?is_overview=true` for acknowledgement times, which PagerDuty drops from the incident once it is resolved.
- One incident is one firing. The incident **title** is the alert name, the PagerDuty **service name** is the service, and the urgency is the severity. The earliest acknowledgement in the log is the acknowledged time; the incident's `resolved_at` (or its resolve log entry) is the resolved time; incidents still open count as unresolved.
- `--pagerduty-service ID` (repeatable) limits the incidents to those services.
- `--save FILE.csv` writes what was fetched in the CSV input format above, so you can re-run the analysis with other settings without calling the API again.
- PagerDuty's list endpoints page by offset and stop at 10,000 records. If the window holds more incidents or log entries than that, the tool stops with an error asking for a shorter `--days` or a service filter, rather than analysing a partial history.

This was tested against recorded API responses, not a live account. If your incident titles carry per-instance detail (host names, numbers), each variant is treated as its own alert; in that case `--save` the history, normalise the `name` column, and analyse the CSV.

### Straight from Opsgenie

With an Opsgenie API key that has the Read access right in `OPSGENIE_API_KEY`, the tool reads the alert list itself:

```bash
export OPSGENIE_API_KEY=...   # API key with Read access
alert-noise-auditor analyze --source opsgenie --days 30 --opsgenie-query 'teams:payments' --save og-history.csv
```

- It sends only `GET /v2/alerts` requests, newest first, and stops paging at the first alert created before the `--days` window.
- One Opsgenie alert is one firing. The alert **message** is the alert name, a `service:<name>` (or `service=<name>`) tag is the service (alerts without one have no service), and the priority (`p1` to `p5`) is the severity.
- The acknowledged and resolved times come from the alert's `report.ackTime` and `report.closeTime`. If an alert is marked acknowledged or closed but the report has no time, its last update time is used. Open alerts count as unresolved.
- `--opsgenie-query QUERY` passes an Opsgenie alert search query (for example `teams:payments` or `tag:prod`) to narrow the alerts.
- `--opsgenie-region eu` uses `api.eu.opsgenie.com` instead of `api.opsgenie.com`.
- Opsgenie lists at most 20,000 alerts per query. If the window holds more, the tool stops with an error asking for a shorter `--days` or a narrower query.

This was tested against a fake API that serves responses in the documented shape, not a live account.

## Options

```text
alert-noise-auditor analyze [--source {file,pagerduty,opsgenie}] [--pagerduty-service ID]
                            [--opsgenie-query QUERY] [--opsgenie-region {us,eu}] [--save CSV]
                            [--input-format {auto,csv,json}] [--format {markdown,json}]
                            [--days N] [--top N] [--flap-gap MIN] [--duplicate-window SEC]
                            [--min-firings N] [--fail-on-delete] [FILE]
```

- `FILE`: the export, or `-` for stdin. Required with the default `--source file`, not allowed with `--source pagerduty` or `opsgenie`. The format comes from the extension, or from the content when there is no `.csv` / `.json` extension.
- `--days N`: use only the last N days of the file, counted back from the newest event in it (not from today). With `--source pagerduty` or `opsgenie` it is the fetch window, counted back from now (default 30).
- `--save CSV`: also write the loaded history as CSV.
- `--format json`: the full per-alert statistics as JSON, for dashboards or further scripting.
- `--top N`: show only the N noisiest alerts in the Markdown report.
- `--flap-gap MIN` (default 30), `--duplicate-window SEC` (default 120), `--min-firings N` (default 3): detection settings, explained below.
- `--fail-on-delete`: exit with status 1 when any alert is recommended for deletion.

Exit status is 0 on success and 2 when the input cannot be read or parsed (the message names the offending line or record) or PagerDuty / Opsgenie cannot be read (missing key, HTTP error).

## How the analysis works

- **Flapping.** A firing is a flap when it starts within `--flap-gap` minutes after the previous firing of the same alert resolved. An alert is flapping when it has at least 3 flaps and they make up at least 30% of its firings. For those, the tool suggests a `for:` duration: the 75th percentile of the alert's resolved durations, rounded up to a whole minute and capped at 15 minutes, and reports how many past firings were no longer than that.
- **Never acknowledged.** An alert with at least `--min-firings` firings and no acknowledgement at all. Alerts acknowledged 25% of the time or less are reported as rarely acknowledged.
- **Duplicates.** Two alerts are duplicates when at least 80% of the rarer one's firings start within `--duplicate-window` seconds of a firing of the other (both need `--min-firings` firings). The one acknowledged less often is reported as the duplicate.
- **Noise score (0 to 100).** 35 x share of firings not acknowledged + 25 x share of firings that are flaps + 25 x volume (firings per day, maxing out at 5 per day) + 15 x duplicate overlap. Without acknowledgement data the other three parts are rescaled to 0-100.
- **Recommendation.** `DELETE` for never-acknowledged alerts; `TUNE` for flapping, duplicate or rarely acknowledged alerts; otherwise `KEEP`. The score ranks alerts; the recommendation comes from these rules, not from a score cut-off.

## Features

- [x] Import from CSV, JSON and Alertmanager-style JSON files
- [x] Import directly from PagerDuty (`--source pagerduty`)
- [x] Import directly from Opsgenie (`--source opsgenie`)
- [ ] Import directly from a live Alertmanager
- [x] Detect flapping, never-acknowledged and duplicate alerts
- [x] Per-alert noise score
- [x] Suggested `for:` duration for flapping alerts
- [ ] Suggested threshold changes
- [x] Report with delete / tune / keep recommendations (Markdown or JSON)

## Limitations

- PagerDuty and Opsgenie are the only live sources. For Alertmanager history you still have to produce the export yourself.
- Opsgenie de-duplicates repeat notifications with the same alias into one alert (its `count` goes up). Those repeats are counted as one firing, so flapping that Opsgenie already folded together is not visible.
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
