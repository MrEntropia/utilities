'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { buildWatchlist, matchEvent, termInText, domainMatches } = require('../src/matcher');

const watchlist = buildWatchlist([
  { name: 'Okta', domains: ['okta.com'], aliases: ['Auth0'], severity: 'high' },
  { name: 'IBM', domains: ['ibm.com'], severity: 'low' },
]);

test('matches on exact domain', () => {
  const hits = matchEvent({ title: 'x', summary: '', domain: 'okta.com' }, watchlist);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].vendor, 'Okta');
  assert.equal(hits[0].field, 'domain');
});

test('matches on subdomain', () => {
  const hits = matchEvent({ title: 'x', summary: '', domain: 'login.okta.com' }, watchlist);
  assert.equal(hits.length, 1);
});

test('matches alias in title text', () => {
  const hits = matchEvent({ title: 'Auth0 customers affected', summary: '', domain: null }, watchlist);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].field, 'text');
  assert.equal(hits[0].reason, 'auth0');
});

test('does not match short term inside another word', () => {
  // "ibm" should not match inside "calibmatic"
  assert.equal(termInText('ibm', 'the calibmatic system'), false);
  assert.equal(termInText('ibm', 'IBM had an incident'), true);
});

test('no false positive when nothing matches', () => {
  const hits = matchEvent({ title: 'Random company breach', summary: 'nothing here', domain: 'random.com' }, watchlist);
  assert.equal(hits.length, 0);
});

test('domainMatches handles exact and subdomain only', () => {
  assert.ok(domainMatches('okta.com', 'okta.com'));
  assert.ok(domainMatches('a.okta.com', 'okta.com'));
  assert.equal(domainMatches('notokta.com', 'okta.com'), false);
  assert.equal(domainMatches('okta.com.evil.com', 'okta.com'), false);
});
