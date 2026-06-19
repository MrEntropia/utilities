'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { checkDomains, withinMonths } = require('../src/check');

const NOW = Date.parse('2026-06-19T00:00:00Z');

function breach(name, breachDate) {
  return { vendor: name, domain: 'vendor.com', raw: { name, breachDate, addedDate: breachDate, pwnCount: 1000, dataClasses: ['Emails'] } };
}

test('withinMonths respects the window boundary', () => {
  assert.equal(withinMonths('2025-06-19', 24, NOW), true); // 12 months ago
  assert.equal(withinMonths('2024-06-20', 24, NOW), true); // ~24 months ago, inside
  assert.equal(withinMonths('2023-06-18', 24, NOW), false); // ~36 months ago
  assert.equal(withinMonths('not-a-date', 24, NOW), false);
});

test('checkDomains flags a breach inside the 24-month window', async () => {
  const fetchByDomain = async () => [breach('Recent', '2025-01-01'), breach('Ancient', '2015-01-01')];
  const [r] = await checkDomains(['vendor.com'], { months: 24, now: NOW, hibpCfg: {} }, { fetchByDomain });
  assert.equal(r.breached, true);
  assert.equal(r.matches.length, 1);
  assert.equal(r.matches[0].raw.name, 'Recent');
  assert.equal(r.totalKnown, 2);
});

test('checkDomains reports clean when only old breaches exist in-window', async () => {
  const fetchByDomain = async () => [breach('Ancient', '2015-01-01')];
  const [r] = await checkDomains(['vendor.com'], { months: 24, now: NOW, hibpCfg: {} }, { fetchByDomain });
  assert.equal(r.breached, false);
  assert.equal(r.totalKnown, 1); // still surfaced as historical context
});

test('checkDomains with months=null counts all history', async () => {
  const fetchByDomain = async () => [breach('Ancient', '2015-01-01')];
  const [r] = await checkDomains(['vendor.com'], { months: null, now: NOW, hibpCfg: {} }, { fetchByDomain });
  assert.equal(r.breached, true);
});

test('checkDomains captures lookup errors per domain', async () => {
  const fetchByDomain = async () => { throw new Error('HTTP 403'); };
  const [r] = await checkDomains(['vendor.com'], { months: 24, now: NOW, hibpCfg: {} }, { fetchByDomain });
  assert.equal(r.breached, false);
  assert.match(r.error, /403/);
});

test('checkDomains sorts matches newest breach first', async () => {
  const fetchByDomain = async () => [breach('Older', '2024-09-01'), breach('Newer', '2025-09-01')];
  const [r] = await checkDomains(['vendor.com'], { months: 24, now: NOW, hibpCfg: {} }, { fetchByDomain });
  assert.deepEqual(r.matches.map((m) => m.raw.name), ['Newer', 'Older']);
});
