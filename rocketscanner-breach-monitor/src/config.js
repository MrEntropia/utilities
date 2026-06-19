'use strict';

const fs = require('fs');
const path = require('path');

// Default RSS sources are free, public breach/news feeds. They are best-effort:
// if a feed moves or goes down, that source is skipped with a warning rather
// than failing the whole run. Override / extend via config.
const DEFAULT_CONFIG = {
  // How many days back to consider an event "recent" on the first run (no
  // state yet). Subsequent runs rely on the seen-state for dedup.
  lookbackDays: 2,
  sources: {
    hibp: {
      enabled: true,
      url: 'https://haveibeenpwned.com/api/v3/breaches',
      // Optional. The public /breaches list does NOT require a key; a key is
      // only needed for domain/email subscriber endpoints.
      apiKeyEnv: 'HIBP_API_KEY',
    },
    ransomwareLive: {
      enabled: true,
      url: 'https://api.ransomware.live/v2/recentvictims',
    },
    rss: {
      enabled: true,
      feeds: [
        { name: 'databreaches', url: 'https://databreaches.net/feed/' },
        { name: 'bleepingcomputer-security', url: 'https://www.bleepingcomputer.com/feed/' },
        { name: 'thehackernews', url: 'https://feeds.feedburner.com/TheHackersNews' },
      ],
    },
  },
  watchlist: [
    // { name: 'Acme Cloud', domains: ['acme.example'], aliases: ['AcmeCorp'], severity: 'high', tags: ['critical-vendor'] }
  ],
  alerters: {
    console: { enabled: true },
    jsonFile: { enabled: true, dir: 'reports' },
  },
};

function deepMerge(base, override) {
  if (Array.isArray(override)) return override.slice();
  if (override && typeof override === 'object') {
    const out = { ...base };
    for (const [k, v] of Object.entries(override)) {
      out[k] = deepMerge(base ? base[k] : undefined, v);
    }
    return out;
  }
  return override === undefined ? base : override;
}

function loadConfig(configPath) {
  let userConfig = {};
  if (configPath) {
    const resolved = path.resolve(configPath);
    if (!fs.existsSync(resolved)) {
      throw new Error(`Config file not found: ${resolved}`);
    }
    userConfig = JSON.parse(fs.readFileSync(resolved, 'utf8'));
  }
  const config = deepMerge(DEFAULT_CONFIG, userConfig);

  // Resolve API keys from the environment (never store secrets in config).
  if (config.sources.hibp && config.sources.hibp.apiKeyEnv) {
    config.sources.hibp.apiKey = process.env[config.sources.hibp.apiKeyEnv] || null;
  }
  return config;
}

module.exports = { loadConfig, DEFAULT_CONFIG, deepMerge };
