'use strict';

const fs = require('fs');
const path = require('path');

// Persists which events we've already alerted on so re-runs don't double-fire.
// Format: { lastRun: ISO, seen: { [eventId]: firstSeenISO } }

const PRUNE_AFTER_DAYS = 90;

function loadState(statePath) {
  try {
    const raw = fs.readFileSync(statePath, 'utf8');
    const parsed = JSON.parse(raw);
    return {
      lastRun: parsed.lastRun || null,
      seen: parsed.seen && typeof parsed.seen === 'object' ? parsed.seen : {},
    };
  } catch (err) {
    if (err.code === 'ENOENT') return { lastRun: null, seen: {} };
    throw new Error(`Could not read state file ${statePath}: ${err.message}`);
  }
}

function pruneSeen(seen, now = Date.now()) {
  const cutoff = now - PRUNE_AFTER_DAYS * 24 * 60 * 60 * 1000;
  const out = {};
  for (const [id, ts] of Object.entries(seen)) {
    const t = new Date(ts).getTime();
    if (Number.isNaN(t) || t >= cutoff) out[id] = ts;
  }
  return out;
}

function saveState(statePath, state) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  const tmp = `${statePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, statePath); // atomic-ish replace
}

module.exports = { loadState, saveState, pruneSeen, PRUNE_AFTER_DAYS };
