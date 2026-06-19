'use strict';

const fs = require('fs');
const path = require('path');
const { bareHost } = require('./normalize');

// Is a vendor with this name or any of these domains already on the watchlist?
function findExisting(watchlist, name, domains) {
  const lname = String(name || '').toLowerCase();
  const dset = new Set((domains || []).map(bareHost).filter(Boolean));
  return (watchlist || []).find((v) => {
    if (lname && String(v.name || '').toLowerCase() === lname) return true;
    return (v.domains || []).some((d) => dset.has(bareHost(d)));
  });
}

// Append a vendor to the watchlist in a config file, preserving the rest.
// Returns { added, reason?, existing? }. Idempotent: never adds a duplicate.
function addVendorToConfigFile(configPath, vendor) {
  const resolved = path.resolve(configPath);
  const cfg = JSON.parse(fs.readFileSync(resolved, 'utf8'));
  cfg.watchlist = Array.isArray(cfg.watchlist) ? cfg.watchlist : [];
  const existing = findExisting(cfg.watchlist, vendor.name, vendor.domains);
  if (existing) return { added: false, reason: 'already-present', existing };
  cfg.watchlist.push(vendor);
  const tmp = `${resolved}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(cfg, null, 2)}\n`);
  fs.renameSync(tmp, resolved);
  return { added: true };
}

module.exports = { findExisting, addVendorToConfigFile };
