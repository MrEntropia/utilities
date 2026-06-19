# rocketscanner-breach-monitor

Monitors public breach-intelligence feeds and alerts when one of **your
watchlisted vendors** shows up. Built for RocketScanner third-party / supply-chain
risk monitoring.

- **Zero runtime dependencies** — Node 18+ built-in `fetch` only.
- **Daily batch by design**, but safe to run as often as you like (dedup state
  prevents duplicate alerts), so it doubles as a near-real-time poller.
- **Pluggable sources and alerters** — start with free feeds + console/JSON,
  add paid feeds or Slack/webhook push later.

## What it watches (free, no API key required)

| Source | What it gives you | Freshness |
|---|---|---|
| [Have I Been Pwned](https://haveibeenpwned.com/api/v3) `/breaches` | Catalogue of disclosed breaches with affected domain + date | Updated as Troy Hunt loads breaches (hours–days after disclosure) |
| [ransomware.live](https://www.ransomware.live/) `/v2/recentvictims` | Newly listed ransomware-extortion victims | Near real-time (minutes–hours after a gang posts) |
| RSS news feeds (DataBreaches.net, BleepingComputer, The Hacker News, …) | Breach/incident reporting | Minutes–hours |

The HIBP `/breaches` list is **public and unauthenticated**. A key
(`HIBP_API_KEY`) is only needed if you later add domain/email *subscriber*
endpoints, and is always read from the environment — never the config file.

## Quick start

```bash
cd rocketscanner-breach-monitor
cp config.example.json config.json     # edit the "watchlist" section
node bin/cli.js --config config.json
```

Each vendor in the watchlist:

```json
{
  "name": "Okta",
  "domains": ["okta.com"],          // matched against breach domains (incl. subdomains)
  "aliases": ["Auth0"],             // extra terms matched in headlines/summaries
  "severity": "high",               // high | medium | low | info
  "tags": ["identity", "critical-vendor"]
}
```

A finding is produced when a vendor matches on **domain** (exact or subdomain)
or on a **word-boundary term** in the title/summary (so `IBM` won't match
`calibmatic`).

## Output

- **Console**: human-readable summary, highest severity first.
- **JSON files** under `reports/`:
  - `findings-YYYY-MM-DD.json` — this run's findings + run metadata.
  - `findings.jsonl` — append-only, one finding per line (drop straight into a
    SIEM / log pipeline).

## CLI

```
--config <path>      Config JSON (watchlist, sources, alerters).
--state <path>       Dedup state file. Default: ./.rocketscanner-state.json
--out <dir>          Output dir for JSON reports.
--lookback-days <n>  Only consider events newer than n days (default 2).
--backfill           Ignore lookback; match the full catalogue (still deduped).
--source <name>      Only run this source (repeatable): hibp|ransomwareLive|rss.
--no-dedup           Don't consult/update state; alert on every match.
--dry-run            Don't write state or report files.
--fail-on-find       Exit code 2 if any findings (drives cron/CI alerting).
--strict             Exit code 1 if any source errored.
--quiet              Suppress console output.
--list-sources       Print enabled sources and exit.
-h --help | -v --version
```

Exit codes: `0` clean, `2` findings (with `--fail-on-find`), `1` source error
(with `--strict`). The first run with no state uses `lookbackDays` so you don't
get alerted on the entire historical catalogue; use `--backfill` once if you
*do* want a current-exposure sweep.

## Scheduling

See [`examples/`](./examples/):

- **`crontab.txt`** — daily (or every-15-min) cron entry.
- **`github-action.yml`** — scheduled GitHub Actions workflow that runs the
  monitor and uploads the report as an artifact / fails the job on findings.

These are examples, not active workflows — copy them where you want them.

## Getting alerts closer to real time

"Daily" is the starting point. Latency, cheapest → most real-time:

1. **Poll more often.** This tool is idempotent (state-deduped), so a
   `*/15 * * * *` cron gives ~15-minute latency on the free feeds at no cost.
   ransomware.live in particular updates within minutes of a gang posting.
2. **WebSub / PubSubHubbub on RSS.** Feeds that advertise a `<link rel="hub">`
   (many do via Google's / Superfeedr's hubs) can *push* new items to a
   callback URL the instant they publish — no polling. A future `--serve` mode
   could expose that callback and run the same matcher.
3. **Vendor push channels.** ransomware.live has a public Telegram channel and
   an RSS feed; bridging Telegram → webhook gives sub-minute alerts.
4. **Paid, push-first breach intel.** When you outgrow free feeds:
   - **HIBP domain subscription** — emails you the moment a *monitored domain*
     appears in a new breach ([details](https://www.troyhunt.com/welcome-to-the-new-have-i-been-pwned-domain-search-subscription-service/)).
   - **Breachsense**, **Recorded Future Third-Party Intelligence**,
     **Black Kite**, **Bitsight Pulse** — webhook/streaming APIs purpose-built
     for third-party breach early warning (credential leaks, leak-site posts,
     extortion activity), often *before* public disclosure.

The architecture is built for this: add a new file under `src/sources/`
(returning the common event shape) or `src/alerters/` (Slack/Teams/generic
webhook) and wire it in — the matcher, dedup, and reporting stay unchanged.

## Architecture

```
bin/cli.js            thin executable wrapper
src/index.js          orchestrator: fetch → recency filter → match → dedup → alert
src/config.js         config load + env-based secrets + defaults
src/matcher.js        watchlist matching (domain + word-boundary terms)
src/state.js          JSON dedup state (atomic write, 90-day prune)
src/http.js           fetch with timeout + retry/backoff
src/normalize.js      common event shape helpers
src/sources/          hibp.js · ransomwareLive.js · rss.js
src/alerters/         console.js · jsonFile.js
test/                 node --test unit + parser tests
```

## Testing

```bash
npm test          # node --test
```

## Notes & limits

- Feeds are best-effort: a 4xx/5xx on one source logs a warning and the run
  continues (use `--strict` to make source errors fail the job).
- HIBP and many news sites bot-block aggressively from data-center IPs; run
  from an allowlisted host or set a contact `User-Agent` if you hit 403s.
- This is **signal, not ground truth** — treat findings as leads to verify,
  not confirmed vendor compromise.
