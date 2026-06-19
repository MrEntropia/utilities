# RocketScanner Breach Monitor — Handoff & Research Brief

A self-contained record of why this utility exists, what was decided, what was
built, the breach-feed/real-time research behind it, and the prioritized next
steps. Hand this (with the code) to a Claude Code CLI session to continue.

---

## 1. Goal

Build a utility for **RocketScanner** that monitors feeds for **vendor
breaches** (third-party / supply-chain risk). Start with a **daily** batch;
investigate **push / near-real-time** options to learn about breaches close to
when they happen.

## 2. Decisions made (locked)

| Decision | Choice | Why |
|---|---|---|
| Language/runtime | **Node.js >=18**, zero deps | Matches repo; built-in `fetch`; easy cron/CI |
| Scope of "vendor breach" | **Vendor watchlist** (names + domains + aliases) | Low-noise, focused on *your* third parties |
| Alerting | **Console + JSON file** to start | Zero external setup; SIEM-ingestable JSONL |
| Freshness | **Daily batch first**, idempotent for frequent polling | Simple now, near-real-time later without re-alerting |

## 3. What was built (v0.2.0)

Three subcommands sharing one HIBP-by-domain core (see `CLAUDE.md` for the file
map and `README.md` for full CLI docs):

1. **`monitor`** (default) — fetch feeds → filter to recency window → match
   watchlist → dedup against state → console + JSON report.
2. **`check <domain...>`** — "was this domain breached?" full history or
   `--months N`; `--all` checks every watchlisted domain. Exit `2` if breached.
3. **`add <name> --domain <d>`** — register vendor on watchlist (idempotent) +
   **24-month** back-check. Always adds; exit `2` if breached in-window so
   onboarding automation flags it for review.

Tests: `node --test` → **23 passing** (matcher, RSS/Atom parser, dedup state,
24-month window filter, per-domain lookup, watchlist registration).

### Feed sources wired (all free, no API key required)
- **HIBP** `GET /api/v3/breaches` (full catalogue) and `?Domain=<d>` (per-domain).
  Public endpoints — a key is only needed for *subscriber* domain/email endpoints.
  Freshness signal = `AddedDate`; breach-recency = `BreachDate`.
- **ransomware.live** `GET /v2/recentvictims` — near-real-time ransomware victims.
  Defensive field reads (API field names have shifted across versions).
- **RSS** (configurable) — DataBreaches.net, BleepingComputer, The Hacker News.
  Minimal built-in RSS 2.0/Atom parser; one bad feed never sinks the run.

---

## 4. Research: getting closer to real time

Daily polling is the floor. The latency ladder, cheapest → richest:

1. **Frequent polling (free, ~5–15 min).** Already supported: dedup state means
   re-runs never double-alert. ransomware.live and RSS update through the day;
   HIBP adds breaches in bursts. Just schedule the existing `monitor` more often.
2. **WebSub / PubSubHubbub (push on RSS).** Some feeds advertise a hub
   (`<link rel="hub">`). Subscribe with a callback URL and the hub POSTs new
   items within seconds. Free where supported; needs a public HTTPS endpoint.
3. **Vendor push channels.** ransomware.live publishes a **Telegram** channel
   (and the API) for near-instant victim disclosures. Telegram Bot API → your
   webhook is a low-effort real-time hop.
4. **Aggregators with streaming/webhooks.** Feedly Streaming API / Google Alerts
   → RSS bridges for broader news coverage.
5. **Paid push-first breach intel** (when budget allows):
   - **HIBP domain subscription** — emails you when a monitored domain appears in
     a new breach (closest official push for HIBP).
   - **Breachsense** — webhooks for credential/stealer-log/leak-site hits.
   - **Recorded Future**, **Black Kite** (pre-disclosure "active threat layer"),
     **Bitsight Pulse** — third-party risk + breach intel with API/alert push.

**Recommended path:** ship daily → move to 15-min polling (one cron change) →
add a Slack/webhook alerter → evaluate HIBP domain subscription + one paid
push feed if real-time SLAs matter. New feeds/channels are drop-in (see below).

### Sources
- HIBP API v3: https://haveibeenpwned.com/api/v3
- HIBP subscriptions: https://haveibeenpwned.com/Subscription
- HIBP domain search service: https://www.troyhunt.com/welcome-to-the-new-have-i-been-pwned-domain-search-subscription-service/
- ransomware.live: https://www.ransomware.live/ and API https://api.ransomware.live/
- ransomware.live → Teams automation example: https://infosecwriteups.com/automating-ransomware-intelligence-feed-ransomware-live-504970b8e08a
- Trusted breach-news sources: https://www.bitsight.com/guides/trusted-sources-for-data-breach-and-other-cybercrime-news
- Third-party breach report (Black Kite): https://blackkite.com/reports/third-party-breach-report-2026
- Recorded Future third-party intel: https://www.recordedfuture.com/products/third-party-intelligence
- Breachsense (HIBP alternatives / webhooks): https://www.breachsense.com/alternatives/have-i-been-pwned/

---

## 5. Roadmap / next prompts for Claude Code

Drop-in extension points: feeds → `src/sources/<name>.js` (return the common
event shape, register in `SOURCE_RUNNERS` + config); channels →
`src/alerters/<name>.js`. Matcher/dedup/reporting are source-agnostic.

Suggested next tasks (paste as prompts):
- **"Add a Slack/Teams webhook alerter (URL from env), used by `monitor`
  findings and `add`/`check` results."**
- **"Switch scheduling to every 15 minutes and confirm dedup prevents repeat
  alerts; update examples/crontab.txt and examples/github-action.yml."**
- **"Add ransomware.live victim history to the `check` command so onboarding
  also flags past ransomware listings, not just HIBP breaches."**
- **"Add a WebSub subscriber mode for RSS feeds that advertise a hub, with a
  small HTTP callback server."**
- **"Add a `--severity-threshold` so only high/critical findings alert."**

## 6. Gotchas
- HIBP and some news sites **bot-block data-center IPs (HTTP 403)**. Run from an
  allowlisted host; endpoints are configurable, parsing is defensive.
- Findings are **leads to verify**, not confirmed vendor compromise.
- Never put API keys in config files — env vars only (`HIBP_API_KEY`).

## 7. Local test pattern (no network)
Spin up a local HTTP server returning fixture JSON and point `sources.hibp.url`
(or `--config`) at `http://127.0.0.1:<port>/breaches`. Used to verify
`check`/`add` window boundaries, idempotent add, and exit codes end-to-end.
