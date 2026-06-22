# alert-noise-auditor

> Analyze alert history to find flapping, never-actioned and duplicate alerts, and suggest threshold changes or deletions.

**Status:** 🚧 Early stage — design and roadmap published, implementation in progress. Stars, issues and ideas welcome.

## Why

Alert fatigue makes on-call miserable and causes real pages to be ignored.

## Planned features

- [ ] Import from PagerDuty, Opsgenie, Alertmanager
- [ ] Detect flapping, never-actioned and duplicate alerts
- [ ] Per-alert noise score
- [ ] Suggested threshold / for-duration changes
- [ ] Report with delete / tune / keep recommendations

## Intended usage

```
alert-noise-auditor analyze --source pagerduty --days 30
```

_The interface above is the target design and may change._

## Roadmap

1. Core implementation of the first feature set
2. Examples and tests
3. CI and first tagged release
4. Docs and integrations

## Contributing

Contributions are welcome. Open an issue to discuss an idea or pick an unchecked item from the feature list.

## License

[MIT](LICENSE)
