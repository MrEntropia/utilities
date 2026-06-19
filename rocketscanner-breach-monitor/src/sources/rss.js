'use strict';

const { fetchWithRetry } = require('../http');
const { bareHost, toIso, stableId, clip } = require('../normalize');

// Dependency-free RSS 2.0 / Atom parser. Handles the subset of the spec that
// breach-news feeds use: <item>/<entry> with title, link, description/summary,
// pubDate/published, guid/id. Not a general-purpose XML parser.

function decodeEntities(str) {
  if (!str) return '';
  return str
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/<[^>]+>/g, ' ') // strip any embedded HTML tags
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&#x?[0-9a-fA-F]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function tag(block, name) {
  // First non-greedy match of <name ...>...</name>.
  const re = new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, 'i');
  const m = block.match(re);
  return m ? decodeEntities(m[1]) : null;
}

function atomLink(block) {
  // Atom: <link href="..."/> (prefer rel="alternate" or no rel).
  const matches = [...block.matchAll(/<link\b[^>]*href="([^"]+)"[^>]*\/?>(?:<\/link>)?/gi)];
  if (!matches.length) return null;
  const alt = matches.find((m) => /rel="alternate"/i.test(m[0]) || !/rel=/i.test(m[0]));
  return (alt || matches[0])[1];
}

function parseFeed(xml, feedName) {
  const items = [];
  const blocks = [...xml.matchAll(/<(item|entry)\b[\s\S]*?<\/\1>/gi)].map((m) => m[0]);
  for (const block of blocks) {
    const title = tag(block, 'title') || '(untitled)';
    const link = tag(block, 'link') || atomLink(block);
    const desc = tag(block, 'description') || tag(block, 'summary') || tag(block, 'content');
    const date = toIso(tag(block, 'pubDate') || tag(block, 'published') || tag(block, 'updated'));
    const guid = tag(block, 'guid') || tag(block, 'id') || link || title;
    items.push({
      id: stableId([`rss:${feedName}`, guid]),
      source: `rss:${feedName}`,
      type: 'news',
      title,
      vendor: null, // RSS items match on free text, not a structured vendor field
      domain: bareHost(link),
      url: link || null,
      date,
      summary: clip(desc),
      raw: { feed: feedName, guid },
    });
  }
  return items;
}

async function fetchRssFeeds(cfg) {
  const feeds = cfg.feeds || [];
  const results = [];
  for (const feed of feeds) {
    try {
      const xml = await fetchWithRetry(feed.url, { as: 'text' });
      results.push(...parseFeed(xml, feed.name || bareHost(feed.url) || 'feed'));
    } catch (err) {
      // One bad feed shouldn't sink the run.
      console.warn(`[rss] skipped ${feed.name || feed.url}: ${err.message}`);
    }
  }
  return results;
}

module.exports = { fetchRssFeeds, parseFeed, decodeEntities };
