'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { parseFeed, decodeEntities } = require('../src/sources/rss');

const RSS = `<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title><![CDATA[Okta discloses breach affecting customers]]></title>
    <link>https://example.com/okta-breach</link>
    <description>Details about the &amp; incident.</description>
    <pubDate>Wed, 18 Jun 2026 10:00:00 GMT</pubDate>
    <guid>https://example.com/okta-breach</guid>
  </item>
</channel></rss>`;

const ATOM = `<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Some vendor incident</title>
    <link rel="alternate" href="https://example.org/post"/>
    <summary>Short summary</summary>
    <published>2026-06-17T08:00:00Z</published>
    <id>tag:example.org,2026:post-1</id>
  </entry>
</feed>`;

test('parses RSS 2.0 item', () => {
  const items = parseFeed(RSS, 'test');
  assert.equal(items.length, 1);
  assert.equal(items[0].title, 'Okta discloses breach affecting customers');
  assert.equal(items[0].url, 'https://example.com/okta-breach');
  assert.equal(items[0].source, 'rss:test');
  assert.ok(items[0].date.startsWith('2026-06-18'));
});

test('parses Atom entry with alternate link', () => {
  const items = parseFeed(ATOM, 'atom');
  assert.equal(items.length, 1);
  assert.equal(items[0].url, 'https://example.org/post');
  assert.ok(items[0].date.startsWith('2026-06-17'));
});

test('decodeEntities strips tags and decodes entities', () => {
  assert.equal(decodeEntities('a &amp; <b>b</b>'), 'a & b');
});
