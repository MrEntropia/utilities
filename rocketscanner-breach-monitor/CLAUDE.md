# CLAUDE.md — rocketscanner-breach-monitor

Context for Claude Code working on this package. Read this first.

## What this is
A zero-dependency Node.js (>=18) utility for **RocketScanner** third-party /
supply-chain risk: it watches public breach-intelligence feeds and alerts when a
**watchlisted vendor** appears, and it vets vendor domains on demand. No runtime
dependencies — uses built-in `fetch`, CommonJS, `node --test`.

## Commands (entry: `bin/cli.js` → `src/index.js` `main(argv)`)
- `monitor` (default) — scan feeds, match watchlist, dedup, alert + JSON report.
- `check <domain...>` — read-only "was this domain breached?" (HIBP per-domain).
  `--months N` to window, `--all` for every watchlisted domain. Exit 2 if breached.
- `add <name> --domain <d>` — register a vendor on the watchlist (idempotent) AND
  back-check the last **24 months** (`--months` to change). Exit 2 if breached.

All three share one HIBP-by-domain core. Secrets come from env vars only
(`HIBP_API_KEY`), never config files.

## Architecture (keep this shape)
```
bin/cli.js            thin wrapper -> src/index.js main()
src/index.js          arg parsing + subcommand dispatch + orchestration
src/config.js         defaults + JSON config merge + env key resolution
src/http.js           fetch w/ timeout + retry/backoff (no deps)
src/normalize.js      common event shape: bareHost/toIso/stableId/clip
src/matcher.js        watchlist match: domain (exact+subdomain) + word-boundary text
src/state.js          atomic dedup state, 90-day prune
src/check.js          checkDomains() + withinMonths() + reportCheck()
src/vendors.js        findExisting() + addVendorToConfigFile() (idempotent)
src/sources/*.js      hibp | ransomwareLive | rss  -> array of normalized events
src/alerters/*.js     console | jsonFile           -> consume findings
```
**Extending = drop-in.** New feed → add `src/sources/<name>.js` returning the
common event shape and register it in `SOURCE_RUNNERS`/config. New alert channel
→ add `src/alerters/<name>.js`. Matcher, dedup, and reporting stay unchanged.

## Conventions
- CommonJS (`require`/`module.exports`), 2-space indent, `'use strict'`.
- No runtime deps. Test/dev only via `node --test`.
- Sources must fail soft (one bad feed/domain never sinks the run); `--strict`
  turns source errors into a non-zero exit for CI.
- The breach window uses `BreachDate` (when it happened); surface older breaches
  as context but don't count them toward the gate.

## Verify before committing
```
node --test            # currently 23 passing
node bin/cli.js --help
```
Live feeds may be blocked by data-center egress/IP bot-rules (HIBP returns 403);
all endpoints are configurable and parsing is defensive. Test against local mock
HTTP servers (see docs/HANDOFF.md for the pattern).

## Open next steps
See `docs/HANDOFF.md` → "Roadmap" (real-time/push, Slack/webhook alerter,
ransomware.live history in `check`).
