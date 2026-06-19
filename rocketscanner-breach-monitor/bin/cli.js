#!/usr/bin/env node
'use strict';

// Thin executable wrapper. All logic lives in src/ so it can be unit-tested
// and embedded by other tools (e.g. RocketScanner) without spawning a process.
require('../src/index.js')
  .main(process.argv.slice(2))
  .then((code) => {
    process.exitCode = code;
  })
  .catch((err) => {
    console.error('[rocketscanner-breach-monitor] fatal:', err && err.stack ? err.stack : err);
    process.exitCode = 1;
  });
