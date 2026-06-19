# Changelog

All notable changes to rocketscanner-breach-monitor are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-06-19

### Added
- `check <domain...>` subcommand: on-demand "was this domain breached?" lookup
  via HIBP's public per-domain breaches endpoint. Defaults to all history;
  `--months <n>` windows it. `--all` checks every watchlisted domain. Exit
  code 2 if any domain is breached within the window.
- `add <name> --domain <d>` subcommand: vendor onboarding. Registers the vendor
  on the watchlist (idempotent) **and** runs a 24-month breach back-check
  (`--months` to change the window). Exit code 2 if breached within the window
  so onboarding automation can flag it for review.
- `fetchHibpByDomain` source helper and shared `normalizeBreach`.

## [0.1.0] - 2026-06-19

### Added
- Initial release: daily-batch breach-feed monitor with vendor watchlist matching.
- Sources (free, no key required): Have I Been Pwned `/breaches`,
  ransomware.live `/v2/recentvictims`, and configurable RSS news feeds.
- Watchlist matching on domain (exact + subdomain) and word-boundary text terms,
  with per-vendor severity and tags.
- Dedup state (atomic write, 90-day prune) so frequent re-runs don't double-alert.
- Alerters: console summary and JSON file reports (`findings-YYYY-MM-DD.json`
  plus append-only `findings.jsonl`).
- CLI flags: `--config`, `--state`, `--out`, `--lookback-days`, `--backfill`,
  `--source`, `--no-dedup`, `--dry-run`, `--fail-on-find`, `--strict`,
  `--quiet`, `--list-sources`.
- Example cron entry and scheduled GitHub Actions workflow.
- Unit tests (`node --test`) for matcher, RSS/Atom parser, and state.
