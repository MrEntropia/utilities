'use strict';

const { fetchWithRetry } = require('../http');
const { bareHost, toIso, stableId, clip } = require('../normalize');

// ransomware.live — recently disclosed ransomware victims (near real-time).
// The v2 API returns an array of victim records. Field names have shifted
// across API versions, so we read defensively from several candidates.
function pick(obj, keys) {
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return obj[k];
  }
  return null;
}

async function fetchRansomwareLive(cfg) {
  const data = await fetchWithRetry(cfg.url, { as: 'json' });
  const list = Array.isArray(data) ? data : data && Array.isArray(data.victims) ? data.victims : null;
  if (!list) {
    throw new Error('ransomware.live: unexpected response (expected an array of victims)');
  }
  return list.map((v) => {
    const victim = pick(v, ['victim', 'post_title', 'title', 'name']) || 'Unknown victim';
    const group = pick(v, ['group_name', 'group', 'gang']) || 'unknown group';
    const domain = bareHost(pick(v, ['domain', 'website', 'url']));
    const date = toIso(pick(v, ['published', 'discovered', 'attackdate', 'date']));
    return {
      id: stableId(['ransomware.live', victim, group, date]),
      source: 'ransomware.live',
      type: 'ransomware',
      title: `${victim} listed by ${group}`,
      vendor: victim,
      domain,
      url: pick(v, ['post_url', 'claim_url', 'screenshot', 'url']),
      date,
      summary: clip(pick(v, ['description', 'summary']) || `Country: ${pick(v, ['country']) || 'n/a'}`),
      raw: {
        group,
        country: pick(v, ['country']),
        activity: pick(v, ['activity', 'sector']),
        published: pick(v, ['published']),
        discovered: pick(v, ['discovered']),
      },
    };
  });
}

module.exports = { fetchRansomwareLive };
