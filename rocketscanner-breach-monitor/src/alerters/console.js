'use strict';

const SEV_ORDER = { high: 0, medium: 1, low: 2, info: 3 };

function topSeverity(findings) {
  return findings
    .flatMap((f) => f.matches.map((m) => m.severity))
    .sort((a, b) => (SEV_ORDER[a] ?? 9) - (SEV_ORDER[b] ?? 9))[0];
}

function report(findings) {
  if (!findings.length) {
    console.log('✓ rocketscanner-breach-monitor: no new watchlisted breaches.');
    return;
  }
  console.log(`\n⚠ rocketscanner-breach-monitor: ${findings.length} new finding(s) — highest severity: ${topSeverity(findings)}\n`);
  for (const f of findings) {
    const vendors = [...new Set(f.matches.map((m) => m.vendor))].join(', ');
    console.log(`• [${f.source}] ${f.title}`);
    console.log(`    vendor(s): ${vendors}`);
    console.log(`    matched on: ${f.matches.map((m) => `${m.field}:${m.reason}`).join(', ')}`);
    if (f.date) console.log(`    date: ${f.date}`);
    if (f.url) console.log(`    url:  ${f.url}`);
    if (f.summary) console.log(`    ${f.summary}`);
    console.log('');
  }
}

module.exports = { report, topSeverity };
