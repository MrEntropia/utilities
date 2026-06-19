'use strict';

// Common event shape produced by every source so downstream matching,
// dedup and alerting can stay source-agnostic.
//
// {
//   id:       string  stable dedup key
//   source:   string  e.g. 'hibp', 'ransomware.live', 'rss:databreaches'
//   type:     'breach' | 'ransomware' | 'news'
//   title:    string
//   vendor:   string|null  best-guess affected org name
//   domain:   string|null  affected domain (lowercased, bare host)
//   url:      string|null  link to the source record
//   date:     string|null  ISO 8601, when the event was published/added
//   summary:  string
//   raw:      object   original source payload (trimmed)
// }

function bareHost(input) {
  if (!input) return null;
  let s = String(input).trim().toLowerCase();
  s = s.replace(/^https?:\/\//, '').replace(/^www\./, '');
  s = s.split('/')[0].split('?')[0].split('#')[0];
  s = s.replace(/[.,;]+$/, '');
  return s || null;
}

function toIso(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function stableId(parts) {
  return parts
    .filter(Boolean)
    .map((p) => String(p).trim().toLowerCase())
    .join('|');
}

function clip(str, max = 500) {
  if (!str) return '';
  const s = String(str).replace(/\s+/g, ' ').trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

module.exports = { bareHost, toIso, stableId, clip };
