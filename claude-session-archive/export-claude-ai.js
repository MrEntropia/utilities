// export-claude-ai.js: save your Claude Code sessions from claude.ai, for `claude-session-archive import`.
//
// claude.ai keeps a copy of every cloud session (claude.ai/code) and of every local session that had
// Remote Control on, often for longer than your computer keeps the transcript. There is no documented
// export, so this reads the same endpoints the claude.ai page itself loads, with your own sign-in,
// and downloads them as gzip-compressed JSON Lines, one session per line.
//
// How to run it:
//   1. Open https://claude.ai/code in your browser, signed in.
//   2. Open the developer console (Chrome: View > Developer > JavaScript Console).
//   3. Read this file, then paste it in and press Enter. Chrome may ask you to type "allow pasting" first.
//   4. Save each file it downloads (claude-ai-sessions-<date>-partN.jsonl.gz). Then, in a terminal:
//        claude-session-archive import ~/Downloads/claude-ai-sessions-*.jsonl.gz
//
// It only reads (GET requests to claude.ai) and only downloads to your own computer. Never paste code
// into a console that you have not read; this file is short so that you can.
//
// Optional: to skip sessions you already have, set SKIP to their ids (the part after "cse_" or "session_").
(async () => {
  const SKIP = new Set([]);
  const MAX_BATCH_BYTES = 120e6; // uncompressed size per downloaded file
  const H = { "anthropic-version": "2023-06-01" };
  const get = async (path) => {
    const r = await fetch(path, { credentials: "include", headers: H });
    if (!r.ok) throw new Error(`${path.split("?")[0]}: HTTP ${r.status}`);
    return r.json();
  };
  const pages = async (path) => {
    const out = [];
    for (let cursor = null, i = 0; i < 5000; i++) {
      const j = await get(path + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""));
      out.push(...j.data);
      if (!j.next_cursor || !j.data.length) break;
      cursor = j.next_cursor;
    }
    return out;
  };
  const date = new Date().toISOString().slice(0, 10);
  let part = 0;
  const save = async (lines) => {
    part++;
    const gz = await new Response(new Blob([lines.join("\n") + "\n"]).stream()
      .pipeThrough(new CompressionStream("gzip"))).blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(gz);
    a.download = `claude-ai-sessions-${date}-part${part}.jsonl.gz`;
    document.body.appendChild(a); a.click(); a.remove();
    console.log(`saved ${a.download}: ${lines.length} sessions, ${(gz.size / 1e6).toFixed(1)} MB`);
  };

  const sessions = (await pages("/v1/code/sessions")).filter((s) => !SKIP.has(s.id.replace(/^(cse|session)_/, "")));
  console.log(`exporting ${sessions.length} sessions`);
  let lines = [], bytes = 0, failed = [];
  for (const [n, s] of sessions.entries()) {
    const id = "session_" + s.id.replace(/^(cse|session)_/, "");
    try {
      const metadata = (await get(`/v1/code/sessions/${id}`)).response_shape;
      const events = (await pages(`/v1/code/sessions/${id}/events`))
        .sort((a, b) => Number(a.sequence_num) - Number(b.sequence_num));
      const line = JSON.stringify({ id, exported_at: new Date().toISOString(), listing: s, metadata, events });
      lines.push(line); bytes += line.length;
      if (bytes > MAX_BATCH_BYTES) { await save(lines); lines = []; bytes = 0; }
    } catch (e) { failed.push(`${id}: ${e.message}`); }
    if ((n + 1) % 10 === 0) console.log(`${n + 1} / ${sessions.length}`);
  }
  if (lines.length) await save(lines);
  console.log(`done: ${part} file(s)` + (failed.length ? `; failed: ${failed.join(", ")}` : ""));
})();
