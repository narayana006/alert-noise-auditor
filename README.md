# alert-noise-auditor

Analyze alert history to find flapping, never-actioned and duplicate alerts, and suggest threshold changes or deletions.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Alert fatigue makes on-call miserable and causes real pages to be ignored.

## Who it is for

On-call teams suffering from too many alerts.

## How it will work

1. You export or connect your alert history.
2. The tool finds alerts that flap, alerts nobody ever acts on and alerts that duplicate each other.
3. It writes a report recommending which alerts to delete, tune or keep, with suggested thresholds.

## Planned features

Unticked items are not built yet.

- [ ] Import from PagerDuty, Opsgenie, Alertmanager
- [ ] Detect flapping, never-actioned and duplicate alerts
- [ ] Per-alert noise score
- [ ] Suggested threshold / for-duration changes
- [ ] Report with delete / tune / keep recommendations

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
alert-noise-auditor analyze --source pagerduty --days 30
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
