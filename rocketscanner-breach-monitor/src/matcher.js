'use strict';

const { bareHost } = require('./normalize');

// Build a fast lookup structure from the configured watchlist.
// Each entry: { name, domains?: string[], aliases?: string[], severity?, tags? }
function buildWatchlist(entries) {
  return (entries || []).map((e) => {
    const name = String(e.name || '').trim();
    const domains = (e.domains || []).map(bareHost).filter(Boolean);
    // Terms are matched against free text (titles/summaries). Keep them
    // reasonably specific (>= 3 chars) to avoid noisy substring hits.
    const terms = [name, ...(e.aliases || [])]
      .map((t) => String(t || '').trim().toLowerCase())
      .filter((t) => t.length >= 3);
    return {
      name,
      domains,
      terms,
      severity: e.severity || 'medium',
      tags: e.tags || [],
    };
  });
}

// Does `term` appear in `text` on a word-ish boundary? Avoids matching
// "ibm" inside "calibmatic" while still catching "IBM Corp.".
function termInText(term, text) {
  if (!text) return false;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, 'i');
  return re.test(text);
}

function domainMatches(eventDomain, watchDomain) {
  if (!eventDomain || !watchDomain) return false;
  // Exact host or subdomain of the watched domain.
  return eventDomain === watchDomain || eventDomain.endsWith(`.${watchDomain}`);
}

/**
 * Return the list of watchlist hits for a single normalized event.
 * Each hit: { vendor, severity, tags, field, reason }
 */
function matchEvent(event, watchlist) {
  const hits = [];
  const haystack = [event.title, event.summary, event.vendor]
    .filter(Boolean)
    .join(' \n ')
    .toLowerCase();
  const eventDomain = bareHost(event.domain);

  for (const entry of watchlist) {
    let matched = null;

    for (const d of entry.domains) {
      if (domainMatches(eventDomain, d)) {
        matched = { field: 'domain', reason: d };
        break;
      }
    }

    if (!matched) {
      for (const t of entry.terms) {
        if (termInText(t, haystack)) {
          matched = { field: 'text', reason: t };
          break;
        }
      }
    }

    if (matched) {
      hits.push({
        vendor: entry.name,
        severity: entry.severity,
        tags: entry.tags,
        field: matched.field,
        reason: matched.reason,
      });
    }
  }
  return hits;
}

module.exports = { buildWatchlist, matchEvent, termInText, domainMatches };
