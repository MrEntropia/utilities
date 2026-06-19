'use strict';

const fs = require('fs');
const path = require('path');

// Writes two artifacts under <dir>:
//   - findings-YYYY-MM-DD.json : the findings from this run (overwritten per day)
//   - findings.jsonl           : append-only log, one finding per line (for SIEM ingest)
function write(findings, cfg, runMeta) {
  const dir = path.resolve(cfg.dir || 'reports');
  fs.mkdirSync(dir, { recursive: true });

  const day = (runMeta.startedAt || new Date().toISOString()).slice(0, 10);
  const dailyPath = path.join(dir, `findings-${day}.json`);
  fs.writeFileSync(
    dailyPath,
    JSON.stringify({ run: runMeta, count: findings.length, findings }, null, 2)
  );

  if (findings.length) {
    const lines = findings.map((f) => JSON.stringify({ detectedAt: runMeta.startedAt, ...f })).join('\n');
    fs.appendFileSync(path.join(dir, 'findings.jsonl'), `${lines}\n`);
  }
  return dailyPath;
}

module.exports = { write };
