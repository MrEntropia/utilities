'use strict';

const path = require('path');
const { loadConfig } = require('./config');
const { loadState, saveState, pruneSeen } = require('./state');
const { buildWatchlist, matchEvent } = require('./matcher');
const { fetchHibp } = require('./sources/hibp');
const { fetchRansomwareLive } = require('./sources/ransomwareLive');
const { fetchRssFeeds } = require('./sources/rss');
const { checkDomains, reportCheck } = require('./check');
const { addVendorToConfigFile } = require('./vendors');
const consoleAlerter = require('./alerters/console');
const jsonFileAlerter = require('./alerters/jsonFile');

const VERSION = require('../package.json').version;

const HELP = `rocketscanner-breach-monitor v${VERSION}
Monitor breach feeds for watchlisted vendors, and vet vendor domains on demand.

Usage:
  rocketscanner-breach-monitor [monitor] [options]   Run the feed monitor (default)
  rocketscanner-breach-monitor check <domain...>     Has a domain ever been breached?
  rocketscanner-breach-monitor add <name> --domain d  Add a vendor + 24-month back-check

Run a subcommand with --help for its options. Common:
  -h, --help        Show this help.
  -v, --version     Show version.

Secrets (e.g. HIBP_API_KEY) are read from the environment, never config files.`;

const MONITOR_HELP = `rocketscanner-breach-monitor monitor — scan feeds for watchlisted vendors

Options:
  --config <path>      Config JSON (watchlist, sources, alerters).
  --state <path>       Dedup state file. Default: ./.rocketscanner-state.json
  --out <dir>          Output dir for JSON reports.
  --lookback-days <n>  Only consider events newer than n days (default 2).
  --backfill           Ignore lookback; match the full catalogue (still deduped).
  --source <name>      Only run this source (repeatable): hibp|ransomwareLive|rss.
  --no-dedup           Don't consult/update state; alert on every match.
  --dry-run            Don't write state or report files.
  --fail-on-find       Exit code 2 if any findings.
  --strict             Exit code 1 if any source errored.
  --quiet              Suppress console output.
  --list-sources       Print enabled sources and exit.`;

const CHECK_HELP = `rocketscanner-breach-monitor check — was a domain breached?

Usage:
  check <domain...> [options]
  check --config config.json --all      Check every watchlisted vendor's domains

Options:
  --months <n>     Only count breaches in the last n months (default: all history).
  --config <path>  Config JSON (for HIBP settings and --all).
  --all            Check all domains in the config watchlist.
  --json           Emit JSON instead of text.
  --strict         Exit code 1 if a lookup errored.
  --quiet          Suppress text output.

Exit code 2 if any checked domain is breached within the window.`;

const ADD_HELP = `rocketscanner-breach-monitor add — register a vendor and back-check 24 months

Usage:
  add <name> --domain <d> [--domain <d2> ...] [options]

Options:
  --domain <d>     Vendor domain (repeatable, at least one required).
  --alias <a>      Extra name/term to match in headlines (repeatable).
  --severity <s>   high|medium|low|info (default: medium).
  --tags <a,b,c>   Comma-separated tags.
  --months <n>     Back-check window (default: 24).
  --config <path>  Config JSON to append the vendor to. Without it, prints the
                   watchlist entry instead of writing.
  --json           Emit JSON instead of text.
  --strict         Exit code 1 if the breach lookup errored.
  --quiet          Suppress text output.

Always registers the vendor for ongoing monitoring (unless already present).
Exit code 2 if the vendor was breached within the back-check window.`;

// ---------------------------------------------------------------- shared

function takeValue(argv, i) {
  const v = argv[i + 1];
  if (v === undefined) throw new Error(`Option ${argv[i]} requires a value`);
  return v;
}

function loadHibpCfg(configPath) {
  const config = loadConfig(configPath);
  return { config, hibpCfg: config.sources.hibp };
}

// ---------------------------------------------------------------- monitor

function parseArgs(argv) {
  const opts = { sources: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => takeValue(argv, (i += 1) - 1);
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
  return Object.keys(SOURCE_RUNNERS).filter((key) => {
    const src = config.sources[key];
    if (!src || !src.enabled) return false;
    if (only.length && !only.includes(key)) return false;
    return true;
  });
}

async function runMonitor(argv) {
  const opts = parseArgs(argv);
  if (opts.help) { console.log(MONITOR_HELP); return 0; }

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

  const findings = [];
  const newlySeen = {};
  for (const ev of events) {
    if (cutoff !== null && ev.date && new Date(ev.date).getTime() < cutoff) continue;
    const matches = matchEvent(ev, watchlist);
    if (!matches.length) continue;
    if (!opts.noDedup && state.seen[ev.id]) continue;
    newlySeen[ev.id] = startedAt;
    findings.push({ ...ev, matches });
  }

  if (!opts.quiet && config.alerters.console.enabled) {
    consoleAlerter.report(findings);
  }
  const runMeta = { startedAt, finishedAt: new Date().toISOString(), sources: sourceStats, errors, scanned: events.length };
  if (!opts.dryRun && config.alerters.jsonFile.enabled) {
    const out = jsonFileAlerter.write(findings, config.alerters.jsonFile, runMeta);
    if (!opts.quiet) console.log(`Report written: ${out}`);
  }

  if (!opts.dryRun && !opts.noDedup) {
    state.seen = pruneSeen({ ...state.seen, ...newlySeen });
    state.lastRun = startedAt;
    saveState(statePath, state);
  }

  if (opts.strict && errors.length) return 1;
  if (opts.failOnFind && findings.length) return 2;
  return 0;
}

// ---------------------------------------------------------------- check

function parseCheckArgs(argv) {
  const opts = { domains: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => takeValue(argv, (i += 1) - 1);
    switch (a) {
      case '--months': opts.months = Number(next()); break;
      case '--config': opts.config = next(); break;
      case '--all': opts.all = true; break;
      case '--json': opts.json = true; break;
      case '--strict': opts.strict = true; break;
      case '--quiet': opts.quiet = true; break;
      case '-h': case '--help': opts.help = true; break;
      default:
        if (a.startsWith('-')) throw new Error(`Unknown option: ${a} (try check --help)`);
        opts.domains.push(a);
    }
  }
  return opts;
}

async function runCheck(argv) {
  const opts = parseCheckArgs(argv);
  if (opts.help) { console.log(CHECK_HELP); return 0; }

  const { config, hibpCfg } = loadHibpCfg(opts.config);
  let domains = opts.domains.slice();
  if (opts.all) {
    for (const v of config.watchlist || []) domains.push(...(v.domains || []));
  }
  domains = [...new Set(domains)];
  if (!domains.length) {
    console.error('check: provide at least one <domain> or use --all with --config.');
    return 1;
  }

  const months = Number.isFinite(opts.months) ? opts.months : null;
  const results = await checkDomains(domains, { months, hibpCfg });

  if (opts.json) {
    console.log(JSON.stringify(results, null, 2));
  } else if (!opts.quiet) {
    reportCheck(results);
  }

  if (opts.strict && results.some((r) => r.error)) return 1;
  return results.some((r) => r.breached) ? 2 : 0;
}

// ---------------------------------------------------------------- add

function parseAddArgs(argv) {
  const opts = { domains: [], aliases: [], tags: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => takeValue(argv, (i += 1) - 1);
    switch (a) {
      case '--domain': opts.domains.push(next()); break;
      case '--alias': opts.aliases.push(next()); break;
      case '--severity': opts.severity = next(); break;
      case '--tags': opts.tags.push(...next().split(',').map((t) => t.trim()).filter(Boolean)); break;
      case '--months': opts.months = Number(next()); break;
      case '--config': opts.config = next(); break;
      case '--json': opts.json = true; break;
      case '--strict': opts.strict = true; break;
      case '--quiet': opts.quiet = true; break;
      case '-h': case '--help': opts.help = true; break;
      default:
        if (a.startsWith('-')) throw new Error(`Unknown option: ${a} (try add --help)`);
        if (!opts.name) opts.name = a;
        else throw new Error(`Unexpected argument: ${a}`);
    }
  }
  return opts;
}

async function runAdd(argv) {
  const opts = parseAddArgs(argv);
  if (opts.help) { console.log(ADD_HELP); return 0; }
  if (!opts.name) { console.error('add: vendor <name> is required (try add --help).'); return 1; }
  if (!opts.domains.length) { console.error('add: at least one --domain is required.'); return 1; }

  const vendor = {
    name: opts.name,
    domains: opts.domains,
    aliases: opts.aliases,
    severity: opts.severity || 'medium',
    tags: opts.tags,
  };

  const { hibpCfg } = loadHibpCfg(opts.config);
  const months = Number.isFinite(opts.months) ? opts.months : 24;
  const results = await checkDomains(vendor.domains, { months, hibpCfg });
  const breached = results.some((r) => r.breached);

  // Register the vendor for ongoing monitoring regardless of breach status —
  // a recently-breached vendor is exactly one you want to keep watching.
  let registration = { added: false, reason: 'no-config' };
  if (opts.config) {
    registration = addVendorToConfigFile(opts.config, vendor);
  }

  if (opts.json) {
    console.log(JSON.stringify({ vendor, windowMonths: months, breached, registration, results }, null, 2));
  } else if (!opts.quiet) {
    console.log(`\nOnboarding check for "${vendor.name}" (${vendor.domains.join(', ')}) — last ${months} months:\n`);
    reportCheck(results);
    console.log('');
    if (registration.added) {
      console.log(`✓ Added "${vendor.name}" to ${opts.config} — now monitored on every run.`);
    } else if (registration.reason === 'already-present') {
      console.log(`• "${vendor.name}" is already in ${opts.config}; left unchanged.`);
    } else {
      console.log('• No --config given; not saved. Add this entry to your watchlist:');
      console.log(JSON.stringify(vendor, null, 2));
    }
    if (breached) {
      console.log(`\n⚠ Heads up: this vendor was breached within the last ${months} months — review before onboarding.`);
    }
  }

  if (opts.strict && results.some((r) => r.error)) return 1;
  return breached ? 2 : 0;
}

// ---------------------------------------------------------------- dispatch

async function main(argv) {
  const args = argv || [];
  const first = args[0];

  if (first === '-v' || first === '--version') { console.log(VERSION); return 0; }
  if (first === '-h' || first === '--help' || first === 'help') { console.log(HELP); return 0; }

  switch (first) {
    case 'check': return runCheck(args.slice(1));
    case 'add': return runAdd(args.slice(1));
    case 'monitor': return runMonitor(args.slice(1));
    default: return runMonitor(args); // backward-compatible default
  }
}

module.exports = { main, parseArgs, parseCheckArgs, parseAddArgs, enabledSources, VERSION };
