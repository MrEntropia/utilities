'use strict';

const { fetchWithRetry } = require('../http');
const { bareHost, toIso, stableId, clip } = require('../normalize');

// Map one raw HIBP breach object to the common event shape.
function normalizeBreach(b) {
  const domain = bareHost(b.Domain);
  const name = b.Title || b.Name || domain || 'Unknown breach';
  return {
    id: stableId(['hibp', b.Name || b.Title || domain]),
    source: 'hibp',
    type: 'breach',
    title: `${name} breach (${b.PwnCount ? b.PwnCount.toLocaleString() : '?'} accounts)`,
    vendor: b.Title || b.Name || null,
    domain,
    url: b.Name ? `https://haveibeenpwned.com/PwnedWebsites#${b.Name}` : null,
    date: toIso(b.AddedDate) || toIso(b.BreachDate),
    summary: clip(b.Description),
    raw: {
      name: b.Name,
      breachDate: b.BreachDate,
      addedDate: b.AddedDate,
      pwnCount: b.PwnCount,
      dataClasses: b.DataClasses,
      isVerified: b.IsVerified,
    },
  };
}

// Have I Been Pwned — public breaches catalogue.
// GET /api/v3/breaches returns every breach HIBP knows about. No API key is
// required for this endpoint. We use AddedDate (when HIBP published it) as the
// freshness signal, since BreachDate can be years in the past.
async function fetchHibp(cfg) {
  const breaches = await fetchWithRetry(cfg.url, {
    as: 'json',
    apiKey: cfg.apiKey || undefined,
  });
  if (!Array.isArray(breaches)) {
    throw new Error('HIBP: unexpected response (expected an array of breaches)');
  }
  return breaches.map(normalizeBreach);
}

// GET /api/v3/breaches?Domain=<domain> filters the public catalogue to breaches
// of that domain. Also unauthenticated. Used by the vendor-vetting `check`
// command to answer "was this domain breached?".
async function fetchHibpByDomain(cfg, domain) {
  const bare = bareHost(domain);
  if (!bare) throw new Error(`HIBP: invalid domain "${domain}"`);
  const sep = cfg.url.includes('?') ? '&' : '?';
  const url = `${cfg.url}${sep}Domain=${encodeURIComponent(bare)}`;
  const breaches = await fetchWithRetry(url, {
    as: 'json',
    apiKey: cfg.apiKey || undefined,
  });
  if (!Array.isArray(breaches)) {
    throw new Error('HIBP: unexpected response (expected an array of breaches)');
  }
  return breaches.map(normalizeBreach);
}

module.exports = { fetchHibp, fetchHibpByDomain, normalizeBreach };
