'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { findExisting, addVendorToConfigFile } = require('../src/vendors');

const watchlist = [
  { name: 'Okta', domains: ['okta.com'] },
  { name: 'Cloudflare', domains: ['cloudflare.com'] },
];

test('findExisting matches by name (case-insensitive)', () => {
  assert.ok(findExisting(watchlist, 'okta', []));
});

test('findExisting matches by domain', () => {
  assert.ok(findExisting(watchlist, 'Different Name', ['www.cloudflare.com']));
});

test('findExisting returns undefined for a new vendor', () => {
  assert.equal(findExisting(watchlist, 'Acme', ['acme.example']), undefined);
});

test('addVendorToConfigFile appends a new vendor', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rsbm-v-'));
  const p = path.join(dir, 'config.json');
  fs.writeFileSync(p, JSON.stringify({ watchlist: watchlist.slice() }));
  const res = addVendorToConfigFile(p, { name: 'Acme', domains: ['acme.example'], severity: 'high' });
  assert.equal(res.added, true);
  const after = JSON.parse(fs.readFileSync(p, 'utf8'));
  assert.equal(after.watchlist.length, 3);
  assert.equal(after.watchlist[2].name, 'Acme');
});

test('addVendorToConfigFile is idempotent for duplicates', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rsbm-v-'));
  const p = path.join(dir, 'config.json');
  fs.writeFileSync(p, JSON.stringify({ watchlist: watchlist.slice() }));
  const res = addVendorToConfigFile(p, { name: 'Okta', domains: ['okta.com'] });
  assert.equal(res.added, false);
  assert.equal(res.reason, 'already-present');
  const after = JSON.parse(fs.readFileSync(p, 'utf8'));
  assert.equal(after.watchlist.length, 2);
});
