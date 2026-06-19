'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { loadState, saveState, pruneSeen } = require('../src/state');

test('save then load round-trips', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rsbm-'));
  const p = path.join(dir, 'state.json');
  saveState(p, { lastRun: '2026-06-19T00:00:00Z', seen: { 'a|b': '2026-06-19T00:00:00Z' } });
  const loaded = loadState(p);
  assert.equal(loaded.lastRun, '2026-06-19T00:00:00Z');
  assert.equal(loaded.seen['a|b'], '2026-06-19T00:00:00Z');
});

test('loadState returns empty on missing file', () => {
  const loaded = loadState(path.join(os.tmpdir(), 'definitely-missing-rsbm.json'));
  assert.deepEqual(loaded, { lastRun: null, seen: {} });
});

test('pruneSeen drops entries older than the window', () => {
  const now = Date.parse('2026-06-19T00:00:00Z');
  const old = new Date(now - 100 * 86400000).toISOString();
  const fresh = new Date(now - 1 * 86400000).toISOString();
  const out = pruneSeen({ oldId: old, freshId: fresh }, now);
  assert.equal(out.oldId, undefined);
  assert.equal(out.freshId, fresh);
});
