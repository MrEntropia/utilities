'use strict';

const path = require('path');
const { loadConfig } = require('./config');
const { loadState, saveState, pruneSeen } = require('./state');
const { buildWatchlist, matchEvent } = require('./matcher');
const { fetchHibp } = require('./sources/hibp');
const { fetchRansomwareLive } = require('./sources/ransomwareLive');
const { fetchRssFeeds } = require('./sources/rss');
const consoleAlerter = require('./alerters/console');
const jsonFileAlerter = require('./alerters/jsonFile');

const VERSION = require('../package.json').version;

const HELP = `rocketscanner-breach-monitor v${VERSION}
Monitor public breach feeds and alert when a watchlisted vendor appears.

Usage:
  rocketscanner-breach-monitor [options]

Options:
  --config <path>      Path to JSON config (watchlist, sources, alerters).
  --state <path>       Path to dedup state file. Default: ./.rocketscanner-state.json
  --out <dir>          Output dir for JSON reports. Overrides config.
  --lookback-days <n>  Only consider events newer than n days. Overrides config.
  --backfill           Ignore lookback; match the full catalogue (still deduped).
  --source <name>      Only run this source (repeatable): hibp|ransomwareLive|rss.
  --no-dedup           Do not consult/update state; alert on every match.
  --dry-run            Do everything except writing state and report files.
  --fail-on-find       Exit code 2 if any findings (useful in CI/cron alerting).
  --strict             Exit code 1 if any source errored.
  --quiet              Suppress the console alerter.
  --list-sources       Print enabled sources and exit.
  -h, --help           Show this help.
  -v, --version        Show version.

Secrets (e.g. HIBP_API_KEY) are read from the environment, never config files.
`;

function parseArgs(argv) {
  const opts = { sources: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => argv[(i += 1)];
    switch (a) {
      case '--config': opts.config = next(); break;
      case '--state': opts.state = next(); break;
      case '--out': opts.out = next(); break;
      case '--lookback-days': opts.lookbackDays = Number(next()); break;
      case '--backfill': opts.backfill = true; break;
      case '--source': opts.sources.push(next()); break;
      case '--no-dedup': opts.noDedup = true; break;
      case '--dry-run': opts.dryRun = true; break;
      case '--fail-on-find': opts.failOnFind = true; break;
      case '--strict': opts.strict = true; break;
      case '--quiet': opts.quiet = true; break;
      case '--list-sources': opts.listSources = true; break;
      case '-h': case '--help': opts.help = true; break;
      case '-v': case '--version': opts.version = true; break;
      default:
        throw new Error(`Unknown option: ${a} (try --help)`);
    }
  }
  return opts;
}

const SOURCE_RUNNERS = {
  hibp: (cfg) => fetchHibp(cfg.sources.hibp),
  ransomwareLive: (cfg) => fetchRansomwareLive(cfg.sources.ransomwareLive),
  rss: (cfg) => fetchRssFeeds(cfg.sources.rss),
};

function enabledSources(config, only) {
  const map = { hibp: 'hibp', ransomwareLive: 'ransomwareLive', rss: 'rss' };
  return Object.keys(SOURCE_RUNNERS).filter((key) => {
    const src = config.sources[key];
    if (!src || !src.enabled) return false;
    if (only.length && !only.includes(map[key])) return false;
    return true;
  });
}

async function main(argv) {
  const opts = parseArgs(argv || []);
  if (opts.help) { console.log(HELP); return 0; }
  if (opts.version) { console.log(VERSION); return 0; }

  const config = loadConfig(opts.config);
  if (opts.out) config.alerters.jsonFile.dir = opts.out;
  if (Number.isFinite(opts.lookbackDays)) config.lookbackDays = opts.lookbackDays;

  const active = enabledSources(config, opts.sources);
  if (opts.listSources) {
    console.log('Enabled sources:', active.join(', ') || '(none)');
    return 0;
  }

  const watchlist = buildWatchlist(config.watchlist);
  if (!watchlist.length) {
    console.warn('[warn] watchlist is empty — no event can match. Edit your config.');
  }

  const statePath = path.resolve(opts.state || '.rocketscanner-state.json');
  const state = opts.noDedup ? { lastRun: null, seen: {} } : loadState(statePath);

  const startedAt = new Date().toISOString();
  const cutoff = opts.backfill ? null : Date.now() - (config.lookbackDays || 2) * 86400000;

  // --- gather events from every active source, tolerating per-source failure
  const settled = await Promise.allSettled(active.map((key) => SOURCE_RUNNERS[key](config)));
  const events = [];
  const sourceStats = {};
  const errors = [];
  settled.forEach((res, idx) => {
    const key = active[idx];
    if (res.status === 'fulfilled') {
      sourceStats[key] = res.value.length;
      events.push(...res.value);
    } else {
      sourceStats[key] = 'error';
      errors.push(`${key}: ${res.reason && res.reason.message ? res.reason.message : res.reason}`);
      console.warn(`[error] source ${key} failed: ${res.reason && res.reason.message}`);
    }
  });

  // --- filter to the freshness window, then match against the watchlist
  const findings = [];
  const newlySeen = {};
  for (const ev of events) {
    if (cutoff !== null && ev.date && new Date(ev.date).getTime() < cutoff) continue;
    const matches = matchEvent(ev, watchlist);
    if (!matches.length) continue;
    if (!opts.noDedup && state.seen[ev.id]) continue; // already alerted
    newlySeen[ev.id] = startedAt;
    findings.push({ ...ev, matches });
  }

  // --- alert
  if (!opts.quiet && config.alerters.console.enabled) {
    consoleAlerter.report(findings);
  }
  const runMeta = { startedAt, finishedAt: new Date().toISOString(), sources: sourceStats, errors, scanned: events.length };
  if (!opts.dryRun && config.alerters.jsonFile.enabled) {
    const out = jsonFileAlerter.write(findings, config.alerters.jsonFile, runMeta);
    if (!opts.quiet) console.log(`Report written: ${out}`);
  }

  // --- persist dedup state
  if (!opts.dryRun && !opts.noDedup) {
    state.seen = pruneSeen({ ...state.seen, ...newlySeen });
    state.lastRun = startedAt;
    saveState(statePath, state);
  }

  if (opts.strict && errors.length) return 1;
  if (opts.failOnFind && findings.length) return 2;
  return 0;
}

module.exports = { main, parseArgs, enabledSources, VERSION };
