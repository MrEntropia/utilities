'use strict';

// Minimal fetch helper: timeout, retries with backoff, sane defaults.
// Uses Node 18+ global fetch (no external deps).

const USER_AGENT = 'rocketscanner-breach-monitor/0.1 (+https://github.com/MrEntropia/utilities)';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Fetch a URL with timeout + retry. Returns the parsed body.
 * @param {string} url
 * @param {object} [opts]
 * @param {'json'|'text'} [opts.as='text']  How to parse the response body.
 * @param {number} [opts.timeoutMs=20000]
 * @param {number} [opts.retries=2]         Extra attempts after the first.
 * @param {object} [opts.headers]
 * @param {string} [opts.apiKey]            Sent as hibp-api-key when provided.
 */
async function fetchWithRetry(url, opts = {}) {
  const { as = 'text', timeoutMs = 20000, retries = 2, headers = {}, apiKey } = opts;
  const finalHeaders = {
    'user-agent': USER_AGENT,
    accept: as === 'json' ? 'application/json' : '*/*',
    ...headers,
  };
  if (apiKey) finalHeaders['hibp-api-key'] = apiKey;

  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { headers: finalHeaders, signal: controller.signal });
      if (!res.ok) {
        // 429/5xx are worth retrying; 4xx (except 429) usually are not.
        const retriable = res.status === 429 || res.status >= 500;
        const err = new Error(`HTTP ${res.status} ${res.statusText} for ${url}`);
        err.status = res.status;
        if (!retriable || attempt === retries) throw err;
        lastErr = err;
      } else {
        const body = as === 'json' ? await res.json() : await res.text();
        return body;
      }
    } catch (err) {
      lastErr = err;
      if (attempt === retries) throw err;
    } finally {
      clearTimeout(timer);
    }
    await sleep(2 ** attempt * 1000); // 1s, 2s, 4s ...
  }
  throw lastErr;
}

module.exports = { fetchWithRetry, USER_AGENT };
