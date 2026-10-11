#!/usr/bin/env python3
"""claude-session-archive: keep your Claude Code session history, back it up, and index it.

Commands:
  install [--archive DIR]   stop transcript auto-deletion, add the daily sync hook, run a first sync
  sync [--if-stale]         copy new and grown transcripts to the archive, rebuild the indexes
  index                     rebuild INDEX.md and SESSIONS.md from the archive
  digest OUTDIR             write one condensed text file per session (for summarising)
  status                    show what is configured, and whether the hook can run
  uninstall                 remove the hook and the installed copy (the archive is left alone)

Standard library only, Python 3.8+. Makes no network requests and runs no other programs:
the archive is an ordinary folder, and any cloud backup comes from that folder being inside
a drive your own sync client uploads.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import socket
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

VERSION = "1.0.2"
CACHE_VERSION = 2  # bump when scan_session() returns new fields, so cached rows are re-read
STALE_SECONDS = 20 * 3600
LOCK_STALE_SECONDS = 2 * 3600
RETENTION_DAYS = 36500
KEEP_BACKUPS = 3
WINDOWS = os.name == "nt"
SCRIPT_NAME = "claude_session_archive.py"

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
PROJECTS = CLAUDE_DIR / "projects"
SETTINGS = CLAUDE_DIR / "settings.json"


def config_dir():
    override = os.environ.get("CLAUDE_SESSION_ARCHIVE_HOME")
    if override:
        return Path(override)
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming") / "claude-session-archive"
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "claude-session-archive"


CONF_DIR = config_dir()
CONF = CONF_DIR / "config.json"
STAMP = CONF_DIR / "last-sync"
LOCK = CONF_DIR / "sync.lock"
LOG = CONF_DIR / "sync.log"
CACHE = CONF_DIR / "index-cache.json"
INSTALLED = CONF_DIR / SCRIPT_NAME
POSIX_SHIM = HOME / ".local" / "bin" / "claude-session-archive"
WINDOWS_SHIM = CONF_DIR / "claude-session-archive.cmd"


class Fail(Exception):
    """A user-facing error: print the message and exit 1, no traceback."""


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def write_atomic(path, text, mode=None, encoding="utf-8"):
    """Write via a temp file in the same folder and rename over the target, so an
    interrupted write never leaves a truncated file. Follows a symlinked target."""
    target = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config():
    try:
        data = json.loads(CONF.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def archive_dir():
    archive = load_config().get("archive")
    if not isinstance(archive, str) or not archive:
        raise Fail(f"not installed ({CONF} missing). Run: install --archive <folder>")
    return Path(archive)


# ---------------------------------------------------------------- sync


def copy_additive(src, archive_root, host):
    """Copy local files into the archive without ever replacing a version with less.

    - New file: copied.
    - Transcript (.jsonl, append-only) that has grown: replaces the archive copy.
    - Any other difference (a smaller transcript, a memory note edited on two computers):
      this computer's version is kept beside it under versions/<host>/, never over it.
    Symlinks are skipped on both sides.
    """
    dst = archive_root / "projects"
    versions = archive_root / "versions" / safe_name(host)
    copied = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        rel = Path(root).relative_to(src)
        for name in files:
            s = Path(root) / name
            d = dst / rel / name
            try:
                if s.is_symlink():
                    continue
                st = s.stat()
                if d.is_symlink():
                    log(f"skip {d}: is a symlink in the archive")
                    continue
                if d.exists():
                    dt = d.stat()
                    if dt.st_size == st.st_size:
                        continue  # same size: transcripts are append-only, notes unchanged in practice
                    if not (name.endswith(".jsonl") and st.st_size > dt.st_size):
                        d = versions / rel / name
                        if d.exists() and d.stat().st_size == st.st_size:
                            continue
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
                copied += 1
            except OSError as exc:
                log(f"skip {s}: {exc}")
    return copied


def write_computer_manifest(src, archive_root, host):
    """Record which sessions this computer holds, in a file only this computer writes
    (all-sessions/computers/<host>.json), so the index can say where each session ran."""
    keys = sorted(p.relative_to(src).as_posix() for p in src.glob("*/*.jsonl") if not p.is_symlink())
    path = archive_root / "computers" / f"{safe_name(host)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"computer": host, "sessions": keys}, indent=1) + "\n")


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:64] or "host"


def acquire_lock():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - LOCK.stat().st_mtime > LOCK_STALE_SECONDS:
            LOCK.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False


def cmd_sync(args):
    archive = archive_dir()
    if args.if_stale and STAMP.exists() and time.time() - STAMP.stat().st_mtime < STALE_SECONDS:
        return 0
    if not archive.is_dir():
        log(f"archive not reachable (cloud drive not mounted?): {archive}")
        return 1
    if not acquire_lock():
        log("another sync is running; skipped")
        return 0
    try:
        STAMP.touch()  # at the start, so session starts during a long sync do not pile up
        host = socket.gethostname()
        copied = 0
        if PROJECTS.is_dir():
            copied = copy_additive(PROJECTS, archive / "all-sessions", host)
            write_computer_manifest(PROJECTS, archive / "all-sessions", host)
        count = build_index(archive)
        write_atomic(archive / "LAST-SYNC.txt", f"{datetime.now():%Y-%m-%d %H:%M} {host}\n")
        log(f"ok: {copied} file(s) copied, {count} sessions indexed")
        return 0
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- index

UNGROUPED = "Ungrouped"

# Where a session ran, from the transcript's "entrypoint". Anything else is shown as written.
SURFACES = {
    "cli": "Terminal",
    "claude-desktop": "Desktop app",
    "claude-vscode": "VS Code",
    "sdk-cli": "SDK / headless",
    "sdk-ts": "SDK",
    "sdk-py": "SDK",
}
# A Remote Control session records the id of its claude.ai counterpart; cse_X is session_X there.
BRIDGE_ID = re.compile(r"cse_[A-Za-z0-9]{8,64}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Links accepted from data/cloud.json: claude.ai only, and no characters that end a Markdown link.
CLOUD_URL = re.compile(r"https://claude\.ai/[A-Za-z0-9_./?=&%-]{1,200}")

CURATED_TEMPLATE = """## Curated projects

Notes on projects whose outputs are worth keeping. This file is yours: the sync only reads it,
and shows it at the end of INDEX.md.
"""


def clean(text, cap=None):
    """One line, safe inside Markdown tables and headings: no newlines, no HTML comments or tags."""
    text = " ".join(str(text or "").split()).replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")
    if cap and len(text) > cap:
        text = text[: cap - 3].rstrip() + "..."
    return text


def first_prompt(msg):
    c = msg.get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str))
    if not isinstance(c, str):
        return None
    c = c.strip()
    if not c or c.startswith("<") or c.startswith("Caveat:"):
        return None
    return " ".join(c.split())


def scan_session(path, src):
    title = ai_title = cwd = prompt = entry = bridge = None
    first_ts = last_ts = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if isinstance(rec.get("customTitle"), str):
                title = rec["customTitle"]
            if isinstance(rec.get("aiTitle"), str):
                ai_title = rec["aiTitle"]
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                first_ts = first_ts or ts
                last_ts = ts
            if not cwd and isinstance(rec.get("cwd"), str):
                cwd = rec["cwd"]
            if not entry and isinstance(rec.get("entrypoint"), str):
                entry = rec["entrypoint"]
            b = rec.get("bridgeSessionId")
            if not bridge and isinstance(b, str) and BRIDGE_ID.fullmatch(b):
                bridge = b
            if prompt is None and rec.get("type") == "user" and isinstance(rec.get("message"), dict):
                prompt = first_prompt(rec["message"])
    if not first_ts:
        return None
    return {
        "id": path.stem,
        "custom_title": title,
        "title": title or ai_title or "(untitled)",
        "cwd": cwd or "",
        "start": first_ts[:10],
        "last": (last_ts or first_ts)[:10],
        "prompt": (prompt or "")[:160],
        "file": path.relative_to(src).as_posix(),
        "entrypoint": entry or "",
        "bridge": bridge or "",
    }


def scan_all(src):
    """Session rows for every transcript, re-reading only files whose size or mtime changed."""
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict) or cache.get("src") != str(src) or cache.get("v") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("rows", {}) if isinstance(cache.get("rows"), dict) else {}
    rows, new = [], {}
    for path in sorted(src.glob("*/*.jsonl")):
        key = path.relative_to(src).as_posix()
        try:
            st = path.stat()
        except OSError:
            continue
        sig = [st.st_size, int(st.st_mtime)]
        hit = old.get(key)
        row = hit["row"] if isinstance(hit, dict) and hit.get("sig") == sig else scan_session(path, src)
        new[key] = {"sig": sig, "row": row}
        if row:
            rows.append(dict(row))
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(CACHE, json.dumps({"v": CACHE_VERSION, "src": str(src), "rows": new}))
    except OSError:
        pass
    return rows


def read_data(path, kind):
    """A user-maintained JSON file: missing is empty; unreadable stops the rebuild (raise)
    rather than silently rendering every summary or group as gone."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise Fail(f"{path} is not valid JSON ({exc}); indexes left as they were")
    if not isinstance(data, dict):
        raise Fail(f"{path} must be a JSON object ({kind}); indexes left as they were")
    return data


def load_groups(data_dir):
    raw = read_data(data_dir / "groups.json", "groups").get("groups", {})
    groups = {}
    if isinstance(raw, dict):
        for name, ids in raw.items():
            if isinstance(ids, list):
                groups[str(name)] = [i for i in ids if isinstance(i, str) and len(i) >= 4]
            else:
                log(f"groups.json: group {name!r} is not a list of ids; ignored")
    return groups


def load_summaries(data_dir):
    out = {}
    for sid, v in read_data(data_dir / "summaries.json", "summaries").items():
        if isinstance(v, dict):
            out[sid] = {k: v[k] for k in ("summary", "keep", "kind") if isinstance(v.get(k), str)}
        else:
            log(f"summaries.json: entry {sid!r} is not an object; ignored")
    return out


def load_computers(root):
    """{session file key: [computer, ...]} from the manifests each computer writes for itself."""
    seen = {}
    for path in sorted((root / "computers").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            host, keys = data["computer"], data["sessions"]
            if not isinstance(host, str) or not isinstance(keys, list):
                raise ValueError("wrong shape")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log(f"skip {path}: {exc}")
            continue
        for k in keys:
            if isinstance(k, str):
                seen.setdefault(k, []).append(host)
    return seen


def load_cloud(data_dir, saved_dir):
    """Cloud sessions (claude.ai/code) listed by hand in data/cloud.json. Their transcripts are
    not on any computer, so this is how they get into the index at all. A copy you saved as
    all-sessions/cloud/session_<id>.json is shown as the transcript."""
    raw = read_data(data_dir / "cloud.json", "cloud sessions").get("sessions", [])
    rows = []
    for i, v in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(v, dict) or not isinstance(v.get("title"), str):
            log(f"cloud.json: entry {i} has no title; ignored")
            continue
        url = v.get("url") if isinstance(v.get("url"), str) else ""
        if url and not CLOUD_URL.fullmatch(url):
            log(f"cloud.json: entry {i} url is not a claude.ai link; shown without it")
            url = ""
        text = {k: v[k] if isinstance(v.get(k), str) else "" for k in
                ("repo", "branch", "started_from", "first_request", "group", "date", "last")}
        start = text["date"] if DATE.fullmatch(text["date"]) else ""
        last = text["last"] if DATE.fullmatch(text["last"]) and text["last"] >= start else start
        rows.append({
            "id": "", "cloud": True, "url": url, "title": v["title"], "custom_title": None,
            "start": start, "last": last, "cwd": text["repo"], "branch": text["branch"],
            "started_from": text["started_from"], "prompt": text["first_request"][:160], "file": "",
            "saved": saved_copy(saved_dir, url),
            "group_hint": text["group"],
            "summary": {k: v[k] for k in ("summary", "keep", "kind") if isinstance(v.get(k), str)},
        })
    return rows


def saved_copy(saved_dir, url):
    m = re.search(r"/(session_[A-Za-z0-9]{8,64})$", url)
    if m and (saved_dir / f"{m.group(1)}.json").is_file():
        return f"{m.group(1)}.json"
    return ""


def link(label, url):
    label = clean(label).replace("[", "\\[").replace("]", "\\]")
    return f"[{label}]({url})" if url else label


def session_url(row):
    if row.get("cloud"):
        return row["url"]
    return f"https://claude.ai/code/session_{row['bridge'][4:]}" if row.get("bridge") else ""


def where(row, computers):
    if row.get("cloud"):
        repo = row["cwd"] + (f" ({row['branch']})" if row["cwd"] and row["branch"] else "")
        return clean(" · ".join(p for p in ("Cloud", row["started_from"], repo) if p))
    surface = SURFACES.get(row.get("entrypoint", ""), row.get("entrypoint", "")) or "unknown app"
    hosts = computers.get(row["file"]) or ["computer not recorded"]
    rc = ", Remote Control" if row.get("bridge") else ""
    return clean(f"{' + '.join(hosts)} · {surface}{rc}")


def group_of(row, by_prefix):
    # A title you set yourself (/rename "Marketing: launch post") wins over groups.json.
    head, sep, rest = (row["custom_title"] or "").partition(": ")
    if sep and rest.strip() and 0 < len(head) <= 40:
        row["title"] = rest
        return head
    if row.get("cloud"):
        return row["group_hint"] or UNGROUPED
    for prefix, name in by_prefix.items():
        if row["id"].startswith(prefix):
            return name
    return UNGROUPED


def first_sentence(text, cap=160):
    return clean(str(text).split(". ")[0].rstrip("."), cap)


def build_index(archive):
    # Built from the archive copy, not ~/.claude/projects: it holds every computer's sessions,
    # so a sync from one computer never drops another computer's sessions from the index.
    src = archive / "all-sessions" / "projects"
    data = archive / "data"
    data.mkdir(parents=True, exist_ok=True)
    groups = load_groups(data)
    summaries = load_summaries(data)
    curated_path = data / "curated.md"
    if not curated_path.exists():
        write_atomic(curated_path, CURATED_TEMPLATE)
    curated = curated_path.read_text(encoding="utf-8-sig")

    by_prefix = {p: name for name, ids in groups.items() for p in ids}
    computers = load_computers(archive / "all-sessions")
    rows = scan_all(src) if src.is_dir() else []
    local_count = len(rows)
    cloud = load_cloud(data, archive / "all-sessions" / "cloud")
    rows += cloud
    for r in rows:
        r["group"] = group_of(r, by_prefix)
    rows.sort(key=lambda r: r["start"], reverse=True)
    names = list(groups) + sorted({r["group"] for r in rows} - set(groups) - {UNGROUPED}) + [UNGROUPED]
    missing = sum(1 for r in rows if not r.get("cloud") and r["id"] not in summaries)
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    counts = f"{local_count} local" + (f", {len(cloud)} cloud" if cloud else "")

    def summary_of(r):
        return r["summary"] if r.get("cloud") else summaries.get(r["id"], {})

    # SESSIONS.md: every session in full.
    full = [
        "# All Claude Code sessions", "",
        f"Generated {stamp} from `all-sessions/projects` and `data/cloud.json` ({counts}). Do not edit: rebuilt on every sync.", "",
        "Resume a local one from a terminal in its project folder: `claude --resume <id>`.",
        "Groups come from a `Group: ` prefix on a title you set with /rename, or from `data/groups.json`.",
        f"Summaries and types come from `data/summaries.json`; {missing} session(s) have none yet.",
        "Cloud sessions (claude.ai/code) are listed by hand in `data/cloud.json`; a transcript you saved as `all-sessions/cloud/session_<id>.json` is linked from its entry.", "",
    ]
    for name in names:
        group_rows = [r for r in rows if r["group"] == name]
        if not group_rows:
            continue
        full += [f"## {clean(name)} ({len(group_rows)})", ""]
        for r in group_rows:
            proj = clean(os.path.basename(r["cwd"].replace("\\", "/")) or r["cwd"])
            dates = (r["start"] if r["start"] == r["last"] else f"{r['start']} to {r['last']}") or "date unknown"
            s = summary_of(r)
            kind = f" · **{clean(s['kind'])}**" if s.get("kind") else ""
            ident = "" if r.get("cloud") else f" · `{clean(r['id'])}`"
            full += [f"### {clean(r['title'])}", "", f"{dates} · {proj or 'no project'}{kind}{ident}", "",
                     f"Ran on: {where(r, computers)}", ""]
            url = session_url(r)
            if url:
                full += [f"On claude.ai: {url}", ""]
            if s.get("summary"):
                full += [clean(s["summary"]), ""]
            if s.get("keep"):
                full += [f"**Keep:** {clean(s['keep'])}", ""]
            if r["prompt"]:
                full += [f"First request: _{clean(r['prompt'])}_", ""]
            if r.get("cloud"):
                saved = f"`all-sessions/cloud/{clean(r['saved'])}` (a copy saved from claude.ai)" if r["saved"] else ""
                full += [f"Transcript: {saved or 'not archived (stored on claude.ai only)'}", ""]
            else:
                full += [f"Transcript: `all-sessions/projects/{clean(r['file'])}`", ""]
    write_atomic(archive / "SESSIONS.md", "\n".join(full))

    # INDEX.md: fully generated, so a rewrite can never lose anything hand-written.
    kinds = {}
    for r in rows:
        k = summary_of(r).get("kind")
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    idx = [
        "# Claude Session Archive", "",
        f"Generated {stamp}. Do not edit this file: it is rebuilt on every sync. Edit the files in `data/` instead.", "",
        "| What | Where |", "|---|---|",
        "| Every session, one line each by group, with its type | this file |",
        "| Every session in full: summary, Keep note, first request, transcript path | [`SESSIONS.md`](SESSIONS.md) |",
        "| Groups, summaries and types | `data/groups.json`, `data/summaries.json` |",
        "| Cloud sessions (claude.ai/code), which have no transcript here | `data/cloud.json` |",
        "| Your notes on curated projects (shown at the end of this file) | `data/curated.md` |",
        "| Raw transcripts and project memory, from every computer | `all-sessions/` |", "",
        "A linked session title opens it on claude.ai: a cloud session, or a local one that had Remote Control on.", "",
        f"## All sessions ({len(rows)}: {counts})", "",
    ]
    if kinds:
        tally = ", ".join(f"{n} {clean(k).lower()}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        idx += [f"By type: {tally}.", ""]
    for name in names:
        group_rows = [r for r in rows if r["group"] == name]
        if not group_rows:
            continue
        idx += [f"### {clean(name)} ({len(group_rows)})", "",
                "| Date | Type | Session | Where | What it was | Id |", "|---|---|---|---|---|---|"]
        for r in group_rows:
            s = summary_of(r)
            what = first_sentence(s.get("summary") or r["prompt"])
            ident = "cloud" if r.get("cloud") else clean(r["id"][:8])
            idx.append(f"| {r['start']} | {clean(s.get('kind', ''))} | {link(r['title'], session_url(r))} "
                       f"| {where(r, computers)} | {what} | `{ident}` |")
        idx.append("")
    idx += ["---", "", curated.rstrip(), ""]
    write_atomic(archive / "INDEX.md", "\n".join(idx))
    return len(rows)


def cmd_index(args):
    print(f"{build_index(archive_dir())} sessions indexed")
    return 0


# ---------------------------------------------------------------- digest


def texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)]
    return []


def cmd_digest(args):
    """One text file per session holding only the user's messages and the assistant's prose
    (no tool output), capped in size: small enough to hand to a model for summarising."""
    if args.cap < 1000:
        raise Fail("--cap must be at least 1000")
    archive = archive_dir()
    out = Path(args.outdir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = archive / "all-sessions" / "projects"
    have = set(load_summaries(archive / "data")) if args.missing_only else set()
    n = 0
    for path in sorted(src.glob("*/*.jsonl")):
        if path.stem in have:
            continue
        parts, title = [], None
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if isinstance(rec.get("customTitle"), str):
                    title = rec["customTitle"]
                msg = rec.get("message")
                if rec.get("isSidechain") or not isinstance(msg, dict):
                    continue
                for t in texts(msg.get("content")):
                    t = t.strip()
                    if not t or t.startswith(("<system-reminder", "<command-", "Caveat:")):
                        continue
                    if rec.get("type") == "user":
                        parts.append(f"USER: {t[:1500]}")
                    elif rec.get("type") == "assistant":
                        parts.append(f"ASSISTANT: {t[:1200]}")
        body = "\n\n".join(parts)
        half = args.cap // 2
        if len(body) > args.cap:
            body = body[:half] + "\n\n[... middle omitted ...]\n\n" + body[-half:]
        write_atomic(out / f"{path.stem}.txt", f"SESSION {path.stem}\nTITLE {title}\n\n{body}\n")
        n += 1
    print(f"{n} digest(s) written to {out}")
    return 0


# ---------------------------------------------------------------- settings.json


def read_settings():
    """(settings dict, raw text or None). Refuses shapes it cannot safely edit."""
    if not SETTINGS.exists():
        return {}, None
    raw = SETTINGS.read_text(encoding="utf-8-sig")
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise Fail(f"{SETTINGS} is not valid JSON ({exc}); fix it first. Nothing was changed.")
    if not isinstance(settings, dict):
        raise Fail(f"{SETTINGS} is not a JSON object; nothing was changed")
    hooks = settings.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise Fail(f'"hooks" in {SETTINGS} is not an object; nothing was changed')
    starts = (hooks or {}).get("SessionStart")
    if starts is not None and not isinstance(starts, list):
        raise Fail(f'"hooks.SessionStart" in {SETTINGS} is not a list; nothing was changed')
    return settings, raw


def write_settings(settings, raw):
    target = Path(os.path.realpath(SETTINGS))
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if raw is not None:
        mode = target.stat().st_mode & 0o777
        backup = target.with_name(f"settings.json.bak-{datetime.now():%Y%m%d-%H%M%S}")
        write_atomic(backup, raw, mode=0o600)
        print(f"backed up settings to {backup}")
        # Prune only backups this tool wrote (exact timestamp name), never anyone else's.
        ours = [p for p in target.parent.glob("settings.json.bak-*") if re.fullmatch(r"settings\.json\.bak-\d{8}-\d{6}", p.name)]
        for old in sorted(ours)[:-KEEP_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass
    write_atomic(target, json.dumps(settings, indent=2, ensure_ascii=False) + "\n", mode=mode)


def is_ours(entry):
    """True for a SessionStart entry that runs this tool (current exec form, or an older shell form)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return False
    for h in entry["hooks"]:
        if not isinstance(h, dict):
            continue
        cmd = h.get("command") if isinstance(h.get("command"), str) else ""
        args = [a for a in h.get("args", []) if isinstance(a, str)] if isinstance(h.get("args"), list) else []
        if any(a.endswith(SCRIPT_NAME) for a in args) and "sync" in args:
            return True
        if "claude-session-archive" in cmd and "sync" not in args:
            return True  # shell-form hook from a pre-release version
    return False


def hook_entry(python):
    # Exec form (command + args): no shell, so no quoting or injection issues and the same
    # entry works on macOS, Linux and Windows. async: never delays the start of a session.
    return {"hooks": [{"type": "command", "command": python,
                       "args": [str(INSTALLED), "sync", "--if-stale"], "async": True}]}


def retention(settings):
    v = settings.get("cleanupPeriodDays")
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


# ---------------------------------------------------------------- install / uninstall / status


def cloud_drive_candidates():
    found = []
    if sys.platform == "darwin":
        cs = HOME / "Library" / "CloudStorage"
        found += sorted(cs.glob("GoogleDrive-*/My Drive")) + sorted(cs.glob("OneDrive*"))
        icloud = HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud.is_dir():
            found.append(icloud)
    elif WINDOWS:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{letter}:/My Drive")
            if p.is_dir():
                found.append(p)
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if os.environ.get(var) and Path(os.environ[var]).is_dir():
                found.append(Path(os.environ[var]))
    return list(dict.fromkeys(found))


def interpreter():
    """The Python the hook should run. The installers pass the stable path they found
    (e.g. /opt/homebrew/bin/python3 rather than a versioned Cellar path that upgrades remove)."""
    chosen = os.environ.get("CLAUDE_SESSION_ARCHIVE_PYTHON")
    if chosen and Path(chosen).exists():
        return str(Path(chosen).absolute())
    return sys.executable


def write_shims(python):
    if WINDOWS:
        # %~dp0 is this .cmd's own folder, so the script path never needs encoding; the
        # interpreter path is written in the console code page cmd.exe reads batch files in.
        line = f'@"{python}" "%~dp0{SCRIPT_NAME}" %*\r\n'
        try:
            data = line.encode("oem")
        except (LookupError, UnicodeEncodeError):
            data = line.encode("utf-8")
        WINDOWS_SHIM.write_bytes(data)
        print(f"command: {WINDOWS_SHIM} (install.ps1 puts its folder on your PATH)")
    else:
        POSIX_SHIM.parent.mkdir(parents=True, exist_ok=True)
        if POSIX_SHIM.exists() and SCRIPT_NAME not in POSIX_SHIM.read_text(encoding="utf-8", errors="replace"):
            print(f"note: {POSIX_SHIM} belongs to something else; left alone. Run: {python} {INSTALLED}")
            return
        write_atomic(POSIX_SHIM, f"#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(INSTALLED))} \"$@\"\n", mode=0o755)
        print(f"command: {POSIX_SHIM}")


def cmd_install(args):
    if args.archive:
        archive = Path(args.archive).expanduser().resolve()
    else:
        cands = cloud_drive_candidates()
        if len(cands) != 1:
            listing = "\n  ".join(str(c) for c in cands) or "(none found)"
            raise Fail("choose where the archive goes with --archive <folder>. Synced drives found:\n  "
                       + listing + "\nfor example: --archive \"<one of those>/Claude Session Archive\"")
        archive = (cands[0] / "Claude Session Archive").resolve()
    if not archive.parent.is_dir():
        raise Fail(f"{archive.parent} does not exist")

    settings, raw = read_settings()  # validate before changing anything
    python = interpreter()
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix) and not os.environ.get("CLAUDE_SESSION_ARCHIVE_PYTHON"):
        print(f"warning: running inside a virtual environment; the hook will use {python}, "
              "which stops working if that environment is deleted")

    archive.mkdir(exist_ok=True)
    (archive / "data").mkdir(exist_ok=True)
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    me = Path(__file__).resolve()
    if me != INSTALLED.resolve():
        shutil.copy2(me, INSTALLED)
    write_atomic(CONF, json.dumps({"archive": str(archive), "python": python, "version": VERSION}, indent=2) + "\n")
    print(f"installed {INSTALLED}\narchive: {archive}\npython:  {python}")
    write_shims(python)

    settings["cleanupPeriodDays"] = max(retention(settings), RETENTION_DAYS)
    if settings.get("hooks") is None:
        settings["hooks"] = {}
    if settings["hooks"].get("SessionStart") is None:
        settings["hooks"]["SessionStart"] = []
    starts = settings["hooks"]["SessionStart"]
    starts[:] = [e for e in starts if not is_ours(e)] + [hook_entry(python)]
    write_settings(settings, raw)
    print(f"transcript auto-deletion off (cleanupPeriodDays = {settings['cleanupPeriodDays']}), daily sync hook added")

    print("first sync (the first one copies everything and can take a while)...")
    try:
        if cmd_sync(argparse.Namespace(if_stale=False)) != 0:
            print("the first sync did not complete; it will run again when a Claude Code session starts")
    except Fail as exc:
        print(f"the first sync did not complete ({exc}); it will run again when a Claude Code session starts")
    return 0


def cmd_uninstall(args):
    try:
        settings, raw = read_settings()
        starts = (settings.get("hooks") or {}).get("SessionStart") if raw is not None else None
        if isinstance(starts, list) and any(is_ours(e) for e in starts):
            starts[:] = [e for e in starts if not is_ours(e)]
            write_settings(settings, raw)
            print("removed the hook")
    except Fail as exc:
        print(f"could not edit settings ({exc}); remove the claude-session-archive SessionStart hook by hand")
    if POSIX_SHIM.exists() and SCRIPT_NAME in POSIX_SHIM.read_text(encoding="utf-8", errors="replace"):
        POSIX_SHIM.unlink()
    for p in (INSTALLED, CONF, STAMP, LOCK, CACHE, WINDOWS_SHIM):
        try:
            p.unlink()
        except OSError:
            pass
    print("removed the installed copy. Left alone: the archive folder, cleanupPeriodDays in settings.json, "
          f"the PATH entry, settings backups and the log ({LOG}).")
    return 0


def cmd_status(args):
    conf = load_config()
    try:
        settings, _ = read_settings()
    except Fail as exc:
        settings = {}
        print(f"settings        {exc}")
    ours = [h for e in (settings.get("hooks") or {}).get("SessionStart") or [] if is_ours(e) for h in e["hooks"]]
    runnable = bool(ours) and all(Path(h.get("command", "")).exists() and
                                  all(Path(a).exists() for a in h.get("args", []) if a.endswith(SCRIPT_NAME))
                                  for h in ours if isinstance(h, dict))
    days = retention(settings)
    print(f"version         {VERSION}")
    print(f"archive         {conf.get('archive', '(not installed)')}")
    print(f"hook            {'installed, runnable' if runnable else 'installed, BROKEN (python or script missing; re-run the installer)' if ours else 'not installed'}")
    print(f"retention       {str(days) + ' days' if days else '30 days (default): transcripts are deleted after 30 days'}")
    print(f"last sync here  {datetime.fromtimestamp(STAMP.stat().st_mtime):%Y-%m-%d %H:%M}" if STAMP.exists() else "last sync here  never")
    print(f"local sessions  {len(list(PROJECTS.glob('*/*.jsonl'))) if PROJECTS.is_dir() else 0}")
    print(f"log             {LOG}")
    return 0


def main(argv=None):
    os.umask(0o077)  # archive, indexes, config and logs readable by you only
    p = argparse.ArgumentParser(prog="claude-session-archive",
                                description="Keep, back up and index Claude Code session history.")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd")
    sub.required = True
    i = sub.add_parser("install", help="set up on this computer")
    i.add_argument("--archive", help="archive folder (default: 'Claude Session Archive' in the one synced drive found)")
    i.set_defaults(fn=cmd_install)
    s = sub.add_parser("sync", help="copy new transcripts and rebuild the indexes")
    s.add_argument("--if-stale", action="store_true", help="skip if synced on this computer in the last 20 hours")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("index", help="rebuild INDEX.md and SESSIONS.md only").set_defaults(fn=cmd_index)
    d = sub.add_parser("digest", help="condensed text per session, for summarising")
    d.add_argument("outdir")
    d.add_argument("--missing-only", action="store_true", help="only sessions with no summary yet")
    d.add_argument("--cap", type=int, default=45000, help="max characters per digest (default 45000)")
    d.set_defaults(fn=cmd_digest)
    sub.add_parser("status", help="show configuration").set_defaults(fn=cmd_status)
    sub.add_parser("uninstall", help="remove the hook and installed copy").set_defaults(fn=cmd_uninstall)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Fail as exc:
        log(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
