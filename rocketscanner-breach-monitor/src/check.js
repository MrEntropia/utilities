'use strict';

const { fetchHibpByDomain } = require('./sources/hibp');
const { bareHost } = require('./normalize');

// True if `dateStr` is within the last `months` months from `now`.
function withinMonths(dateStr, months, now = Date.now()) {
  const t = Date.parse(dateStr);
  if (Number.isNaN(t)) return false;
  const cutoff = new Date(now);
  cutoff.setMonth(cutoff.getMonth() - months);
  return t >= cutoff.getTime();
}

function byBreachDateDesc(a, b) {
  return Date.parse(b.raw.breachDate || 0) - Date.parse(a.raw.breachDate || 0);
}

/**
 * Look up breach history for one or more domains via HIBP.
 * @param {string[]} domains
 * @param {object} o
 * @param {number|null} o.months  Only count breaches whose BreachDate is within
 *                                this many months. null = all history.
 * @param {object} o.hibpCfg      HIBP source config ({ url, apiKey }).
 * @param {number} [o.now]
 * @param {object} [deps]         { fetchByDomain } — injectable for tests.
 * @returns {Promise<Array>} per-domain results
 */
async function checkDomains(domains, o, deps = {}) {
  const fetchByDomain = deps.fetchByDomain || fetchHibpByDomain;
  const months = o.months == null ? null : o.months;
  const now = o.now || Date.now();
  const results = [];

  for (const raw of domains) {
    const domain = bareHost(raw);
    const result = { domain, windowMonths: months, breached: false, matches: [], totalKnown: 0, error: null };
    if (!domain) {
      result.error = `invalid domain "${raw}"`;
      results.push(result);
      continue;
    }
    try {
      const all = await fetchByDomain(o.hibpCfg, domain);
      result.totalKnown = all.length;
      const inWindow = months == null ? all.slice() : all.filter((b) => withinMonths(b.raw.breachDate, months, now));
      inWindow.sort(byBreachDateDesc);
      result.matches = inWindow;
      result.breached = inWindow.length > 0;
    } catch (err) {
      result.error = err && err.message ? err.message : String(err);
    }
    results.push(result);
  }
  return results;
}

// Pretty console output for check/add results.
function reportCheck(results) {
  for (const r of results) {
    const scope = r.windowMonths == null ? 'ever' : `in the last ${r.windowMonths} months`;
    if (r.error) {
      console.log(`? ${r.domain}: could not check — ${r.error}`);
      continue;
    }
    if (!r.breached) {
      const extra = r.totalKnown > 0 && r.windowMonths != null
        ? ` (but ${r.totalKnown} older breach(es) on record)`
        : '';
      console.log(`✓ ${r.domain}: no breach ${scope}${extra}`);
      continue;
    }
    console.log(`⚠ ${r.domain}: BREACHED ${scope} — ${r.matches.length} breach(es):`);
    for (const b of r.matches) {
      const dc = (b.raw.dataClasses || []).slice(0, 5).join(', ');
      console.log(`    • ${b.raw.name || b.vendor} — breach date ${b.raw.breachDate || '?'}, added ${b.raw.addedDate || '?'}`);
      console.log(`      ${b.raw.pwnCount ? b.raw.pwnCount.toLocaleString() + ' accounts' : 'unknown size'}${dc ? `; data: ${dc}` : ''}`);
    }
  }
}

module.exports = { checkDomains, withinMonths, reportCheck, byBreachDateDesc };
