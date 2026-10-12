#!/usr/bin/env python3
"""claude-session-archive: keep your Claude Code session history, back it up, and index it.

Commands:
  install [--archive DIR]   stop transcript auto-deletion, add the daily sync hook, run a first sync
  sync [--if-stale]         copy new and grown transcripts to the archive, rebuild the indexes
  index                     rebuild INDEX.md and SESSIONS.md from the archive
  digest OUTDIR             write one condensed text file per session (for summarising)
  import FILE...            file sessions exported from claude.ai into the archive
  secrets                   scan the archive for credentials; report them masked in SECRETS.md
  status                    show what is configured, and whether the hook can run
  uninstall                 remove the hook and the installed copy (the archive is left alone)

Standard library only, Python 3.8+. Makes no network requests and runs no other programs:
the archive is an ordinary folder, and any cloud backup comes from that folder being inside
a drive your own sync client uploads.
"""
import argparse
import bisect
import gzip
import hashlib
import hmac
import secrets as random_bytes
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

VERSION = "1.2.0"
CACHE_VERSION = 4  # bump when scan_session() returns new fields, so cached rows are re-read
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
SAVED_CACHE = CONF_DIR / "saved-cache.json"
SECRETS_CACHE = CONF_DIR / "secrets-cache.json"
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
        try:  # a failed scan must never stop the archive from being kept
            write_secrets_report(archive, *scan_secrets(archive))
        except (Fail, OSError, ValueError) as exc:
            log(f"secrets scan skipped: {exc}")
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
# A session id as claude.ai names it; also the file name of a saved copy (all-sessions/cloud/<id>.json).
SESSION_ID = re.compile(r"session_[A-Za-z0-9]{8,64}")
# How a session was started, from claude.ai's record of it. Anything else is shown as written.
ORIGINS = {"android": "Android app", "ios": "iOS app", "web": "Web", "claude_code_cli": "Terminal"}
# Links accepted from data/cloud.json: claude.ai only, and no characters that end a Markdown link.
CLOUD_URL = re.compile(r"https://claude\.ai/[A-Za-z0-9_./?=&%-]{1,200}")

CURATED_TEMPLATE = """## Curated projects

Notes on projects whose outputs are worth keeping. This file is yours: the sync only reads it,
and shows it at the end of INDEX.md.
"""


def clean(text, cap=None):
    """One line, safe inside Markdown tables and headings: no newlines, no HTML tags, and no
    Markdown links, images or auto-linked URLs, whatever the text came from. The only links in the
    indexes are the ones link() builds, to claude.ai."""
    text = " ".join(str(text or "").split()).replace("\\", "\\\\")
    text = text.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")
    # Renderers turn bare URLs into links; an entity looks the same but is never linked.
    text = re.sub(r"(?i)(?<=[a-z])://", "&#58;//", text)
    text = re.sub(r"(?i)\bwww\.", "www&#46;", text)
    if cap and len(text) > cap:
        text = text[: cap - 3].rstrip() + "..."
    return text


def day(ts):
    """YYYY-MM-DD from a timestamp, or "" if it does not start with one."""
    return ts[:10] if isinstance(ts, str) and DATE.fullmatch(ts[:10]) else ""


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
        "start": day(first_ts),
        "last": day(last_ts or first_ts) or day(first_ts),
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


def event_text(event):
    """(role, text) of a claude.ai session event, or None: user and assistant prose only."""
    p = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(p, dict) or event.get("event_type") not in ("user", "assistant"):
        return None
    if p.get("parent_tool_use_id") or not isinstance(p.get("message"), dict):
        return None  # a subagent's turn, or not a message
    text = " ".join(t.strip() for t in texts(p["message"].get("content")) if t.strip())
    if not text or text.startswith(("<", "Caveat:")):
        return None
    return event["event_type"], text


def scan_saved_file(path):
    """A row for a saved claude.ai session, or None (logged) if the record cannot be read."""
    try:
        return read_saved_file(path)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError) as exc:
        log(f"skip {path.name}: unreadable saved session ({type(exc).__name__})")
        return None


def read_saved_file(path):
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"skip {path}: {exc}")
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("events"), list):
        log(f"skip {path}: not a saved session")
        return None
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    listing = rec.get("listing") if isinstance(rec.get("listing"), dict) else {}

    def field(key):
        v = meta.get(key, listing.get(key))
        return v if isinstance(v, str) else ""

    config = meta.get("config") if isinstance(meta.get("config"), dict) else {}
    repo = branch = ""
    outcomes = config.get("outcomes")
    for o in outcomes if isinstance(outcomes, list) else []:
        g = o.get("git_info") if isinstance(o, dict) else None
        if isinstance(g, dict) and isinstance(g.get("repo"), str):
            repo = g["repo"]
            b = g.get("branches")
            branch = b[0] if isinstance(b, list) and b and isinstance(b[0], str) else ""
            break
    ext = meta.get("external_metadata") if isinstance(meta.get("external_metadata"), dict) else {}
    cur = ext.get("current_branches")
    if not repo and isinstance(cur, dict):
        for k, v in cur.items():
            if isinstance(k, str):
                repo, branch = k, v if isinstance(v, str) else ""
                break
    prompt = ""
    for e in rec["events"]:
        t = event_text(e) if isinstance(e, dict) else None
        if t and t[0] == "user":
            prompt = " ".join(t[1].split())[:160]
            break
    origin = config.get("origin") if isinstance(config.get("origin"), str) else ""
    return {
        "id": path.stem, "kind": field("environment_kind"), "title": field("title") or "(untitled)",
        "start": day(field("created_at")), "last": day(field("last_event_at")),
        "started_from": ORIGINS.get(origin, origin), "repo": repo, "branch": branch, "prompt": prompt,
        "saved": path.name,
    }


def scan_saved(saved_dir):
    """Rows for every saved claude.ai session, re-reading only files whose size or mtime changed."""
    try:
        cache = json.loads(SAVED_CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict) or cache.get("v") != CACHE_VERSION or cache.get("src") != str(saved_dir):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("rows", {}) if isinstance(cache.get("rows"), dict) else {}
    rows, new = [], {}
    for path in sorted(saved_dir.glob("session_*.json")) if saved_dir.is_dir() else []:
        if path.is_symlink() or not SESSION_ID.fullmatch(path.stem):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        sig = [st.st_size, int(st.st_mtime)]
        hit = old.get(path.name)
        row = hit["row"] if isinstance(hit, dict) and hit.get("sig") == sig else scan_saved_file(path)
        new[path.name] = {"sig": sig, "row": row}
        if row:
            rows.append(dict(row))
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(SAVED_CACHE, json.dumps({"v": CACHE_VERSION, "src": str(saved_dir), "rows": new}))
    except OSError:
        pass
    return rows


def load_cloud(data_dir, saved):
    """Cloud sessions (claude.ai/code) listed by hand in data/cloud.json. A saved copy with the
    same session id (see `import`) fills in whatever the entry leaves blank; the entry wins."""
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
        m = re.search(r"/(session_[A-Za-z0-9]{8,64})(?:[?#][^\\s]{0,200})?$", url)
        sid = m.group(1) if m else ""
        copy = saved.get(sid, {})
        rows.append({
            "id": sid, "cloud": True, "url": url, "title": v["title"], "custom_title": None,
            "start": start or copy.get("start", ""), "last": last or copy.get("last", "") or start,
            "cwd": text["repo"] or copy.get("repo", ""), "branch": text["branch"] or copy.get("branch", ""),
            "started_from": text["started_from"] or copy.get("started_from", ""),
            "prompt": text["first_request"][:160] or copy.get("prompt", ""), "file": "",
            "saved": copy.get("saved", ""), "group_hint": text["group"],
            "summary": {k: v[k] for k in ("summary", "keep", "kind") if isinstance(v.get(k), str)},
        })
    return rows


def saved_row(r):
    """An index row for a saved claude.ai session that no local transcript or cloud.json entry covers."""
    cloud = r["kind"] == "anthropic_cloud"
    return {
        "id": r["id"], "cloud": cloud, "recovered": not cloud, "url": f"https://claude.ai/code/{r['id']}",
        "title": r["title"], "custom_title": None, "start": r["start"], "last": r["last"] or r["start"],
        "cwd": r["repo"], "branch": r["branch"], "started_from": r["started_from"], "prompt": r["prompt"],
        "file": "", "saved": r["saved"], "group_hint": "", "summary": {}, "entrypoint": "", "bridge": "",
        "kind": r["kind"],
    }


def link(label, url):
    label = clean(label)
    return f"[{label}]({url})" if url and CLOUD_URL.fullmatch(url) else label


def session_url(row):
    if row.get("cloud") or row.get("recovered"):
        return row["url"]
    return f"https://claude.ai/code/session_{row['bridge'][4:]}" if row.get("bridge") else ""


def where(row, computers):
    if row.get("cloud"):
        repo = row["cwd"] + (f" ({row['branch']})" if row["cwd"] and row["branch"] else "")
        return clean(" · ".join(p for p in ("Cloud", row["started_from"], repo) if p))
    if row.get("recovered"):
        how = row["started_from"] or "unknown app"
        how += ", Remote Control" if row.get("kind") == "bridge" else (f", {row['kind']}" if row.get("kind") else "")
        return clean(" · ".join(p for p in ("Recovered from claude.ai", how, row["cwd"]) if p))
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
    if row.get("group_hint"):
        return row["group_hint"]
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
    saved = {r["id"]: r for r in scan_saved(archive / "all-sessions" / "cloud")}
    used = set()
    for r in rows:  # a local Remote Control session whose claude.ai copy was also saved
        sid = "session_" + r["bridge"][4:] if r.get("bridge") else ""
        if sid in saved:
            r["also_saved"] = saved[sid]["saved"]
            used.add(sid)
    cloud = load_cloud(data, saved)
    used.update(r["id"] for r in cloud)
    extra = [saved_row(r) for sid, r in sorted(saved.items()) if sid not in used]
    cloud += [r for r in extra if r["cloud"]]
    recovered = [r for r in extra if r["recovered"]]
    rows += cloud + recovered
    for r in rows:
        r["group"] = group_of(r, by_prefix)
    rows.sort(key=lambda r: r["start"], reverse=True)
    names = list(groups) + sorted({r["group"] for r in rows} - set(groups) - {UNGROUPED}) + [UNGROUPED]
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    counts = f"{local_count} local" + (f", {len(recovered)} recovered" if recovered else "") + (f", {len(cloud)} cloud" if cloud else "")

    def summary_of(r):
        return (r.get("summary") or {}) if r.get("cloud") and r.get("summary") else summaries.get(r["id"], {})

    missing = sum(1 for r in rows if not summary_of(r).get("summary"))

    # SESSIONS.md: every session in full.
    full = [
        "# All Claude Code sessions", "",
        f"Generated {stamp} from `all-sessions/` and `data/cloud.json` ({counts}). Do not edit: rebuilt on every sync.", "",
        "Resume a local one from a terminal in its project folder: `claude --resume <id>`.",
        "Groups come from a `Group: ` prefix on a title you set with /rename, or from `data/groups.json`.",
        f"Summaries and types come from `data/summaries.json`; {missing} session(s) have none yet.",
        "Cloud sessions (claude.ai/code) come from `data/cloud.json` and from copies saved with `import`; "
        "recovered sessions are Remote Control sessions whose local transcript is gone but whose claude.ai copy was saved.", "",
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
            ident = f" · `{clean(r['id'])}`" if r["id"] else ""
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
            if r.get("cloud") or r.get("recovered"):
                copy = f"`all-sessions/cloud/{clean(r['saved'])}` (a copy saved from claude.ai)" if r["saved"] else ""
                full += [f"Transcript: {copy or 'not archived (stored on claude.ai only)'}", ""]
            else:
                full += [f"Transcript: `all-sessions/projects/{clean(r['file'])}`", ""]
                if r.get("also_saved"):
                    full += [f"Also saved from claude.ai: `all-sessions/cloud/{clean(r['also_saved'])}`", ""]
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
        "| Cloud sessions (claude.ai/code) | `data/cloud.json`, and copies saved with `import` |",
        "| Copies of sessions saved from claude.ai (cloud and recovered) | `all-sessions/cloud/` |",
        "| Your notes on curated projects (shown at the end of this file) | `data/curated.md` |",
        "| Raw transcripts and project memory, from every computer | `all-sessions/` |", "",
        "A linked session title opens it on claude.ai: a cloud session, or one that had Remote Control on.", "",
    ]
    n_secrets = secrets_summary(archive)
    if n_secrets:
        idx += [f"**Secrets:** {n_secrets} credential-shaped value(s) are stored in this archive; see [`SECRETS.md`](SECRETS.md) (values masked).", ""]
    idx += [
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
            ident = "cloud" if r.get("cloud") else clean(r["id"][8:16] if r.get("recovered") else r["id"][:8])
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
    # Sessions saved from claude.ai (see `import`), unless a local transcript already covers them.
    local = {"session_" + r["bridge"][4:] for r in (scan_all(src) if src.is_dir() else []) if r.get("bridge")}
    for row in scan_saved(archive / "all-sessions" / "cloud"):
        sid = row.get("id")
        if not (isinstance(sid, str) and SESSION_ID.fullmatch(sid) and row.get("saved") == f"{sid}.json"):
            continue  # a cache row that does not describe a saved copy
        if sid in have or sid in local:
            continue
        try:
            rec = json.loads((archive / "all-sessions" / "cloud" / row["saved"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parts = []
        for e in rec.get("events") or []:
            t = event_text(e)
            if t:
                parts.append(f"USER: {t[1][:1500]}" if t[0] == "user" else f"ASSISTANT: {t[1][:1200]}")
        body = "\n\n".join(parts)
        half = args.cap // 2
        if len(body) > args.cap:
            body = body[:half] + "\n\n[... middle omitted ...]\n\n" + body[-half:]
        write_atomic(out / f"{sid}.txt", f"SESSION {sid}\nTITLE {row['title']}\n\n{body}\n")
        n += 1
    print(f"{n} digest(s) written to {out}")
    return 0


# ---------------------------------------------------------------- secrets

# Credential formats with a recognisable shape. Every quantifier is bounded, because these run
# over transcript lines that can be megabytes long. Order matters: the first match at a position wins.
SECRET_PATTERNS = [
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
                               r"(?:\s|\\[nr]){0,6}(?P<value>[A-Za-z0-9+/=]{40,200})?")),
    ("Anthropic API key", re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,200}")),
    ("OpenAI API key", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,200}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,64}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,200}")),
    ("Slack webhook", re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,120}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,200}")),
    ("Google API key", re.compile(r"(?<![A-Za-z0-9+/_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}")),
    ("Database URL with password", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:@/\s\"'\\]{1,100}:(?P<value>[^@/\s\"'\\]{3,200})@")),
    ("JSON Web Token (usually short-lived)", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,2000}\.eyJ[A-Za-z0-9_-]{10,4000}\.[A-Za-z0-9_-]{10,2000}")),
    ("Password or secret assignment", re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?!from_secret)(?:[A-Za-z0-9]{1,20}_){0,4}(?:password|passwd|secret|secret_key|secret_access_key|api_key|"
        r"access_token|auth_token|private_key|client_secret)(?:=|\s{0,3}:\s{1,3})(?P<value>[^\s\"'`\\<>,;(){}\[\]]{8,200})")),
    ("Password or secret assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api_?key|access_?token|auth_?token|client_secret)\\?[\"']?\s{0,3}[:=]\s{0,3}\\?[\"']"
        r"(?P<value>[^\"'\s\\<>]{8,200})\\?[\"']")),
]
UNQUOTED_ASSIGNMENT = next(rx for k, rx in SECRET_PATTERNS if k == "Password or secret assignment")
# Values that are obviously not real: placeholders, examples, variable references, masks.
PLACEHOLDER = re.compile(r"(?i)(?:x{6}|\*{4}|\.{3}|example|placeholder|changeme|your[_-]|<|\$\{|\$[A-Z_]|redacted|dummy|sample|test|correct-horse)")
# A literal every match of a pattern contains, so a pattern only runs on lines that could match.
# Lines are lowercased for the check when the anchor is lowercase and the pattern ignores case.
SECRET_ANCHORS = {
    "Private key": ("PRIVATE KEY",), "Anthropic API key": ("sk-ant-",), "OpenAI API key": ("sk-",),
    "AWS access key id": ("AKIA", "ASIA"), "GitHub token": ("gh", "github_pat_"), "GitLab token": ("glpat-",),
    "Slack token": ("xox",), "Slack webhook": ("hooks.slack.com",), "Stripe live key": ("_live_",),
    "Google API key": ("AIza",), "npm token": ("npm_",), "Database URL with password": ("://",),
    "JSON Web Token (usually short-lived)": ("eyJ",),
}
SECRET_WORDS = ("password", "passwd", "pwd", "secret", "api_key", "apikey", "access_token", "accesstoken", "private_key",
                "auth_token", "authtoken", "client_secret")
# Kinds whose shape alone is weak evidence: the value must also look random (digits and mixed case).
NEEDS_RANDOM = ("OpenAI API key",)
# An unquoted value that is a plain word or a dotted name is code (password: string, process.env.X).
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_]{0,199}|[A-Za-z_][A-Za-z0-9_]{0,99}(?:\.[A-Za-z_][A-Za-z0-9_]{0,99}){1,10}")
SECRET_SUFFIXES = (".jsonl", ".json", ".md", ".txt")


PASSWORD_KINDS = ("Password or secret assignment", "Database URL with password")


def mask(value, kind=""):
    """Enough to recognise a value you know, not enough to recover one you don't. Passwords show
    only their length; keys, which start with a public format prefix, show a little more."""
    if kind in PASSWORD_KINDS:
        return f"({len(value)} chars)"
    if len(value) < 20:
        return f"{value[:2]}… ({len(value)} chars)"
    if len(value) < 40:
        return f"{value[:4]}… ({len(value)} chars)"
    return f"{value[:4]}…{value[-2:]} ({len(value)} chars)"


def secrets_salt(archive):
    """A random key kept in the archive (data/.secrets-salt), so fingerprints in SECRETS.md cannot
    be used to test guesses by someone who has only that file."""
    path = archive / "data" / ".secrets-salt"
    try:
        salt = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", salt):
            return salt
    except OSError:
        pass
    salt = random_bytes.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, salt + "\n")
    return salt


def fingerprint(value, salt=""):
    return hmac.new(salt.encode(), value.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:12]


def looks_random(value):
    return any(c.isdigit() for c in value) and any(c.islower() for c in value) and any(c.isupper() for c in value)


def find_secrets(line):
    """(kind, value) for each credential in a line; a span already claimed is not matched twice."""
    # In transcripts a key often follows a JSON escape such as \\n; the letter would defeat the
    # word-boundary checks below, so escapes are turned into spaces first.
    line = line.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    taken = []
    lower = None
    for kind, rx in SECRET_PATTERNS:
        anchors = SECRET_ANCHORS.get(kind)
        if anchors is not None:
            if not any(a in line for a in anchors):
                continue
        else:
            lower = lower if lower is not None else line.lower()
            if not any(w in lower for w in SECRET_WORDS):
                continue
        for m in rx.finditer(line):
            value = m.group("value") if "value" in rx.groupindex else m.group(0)
            if value is None:  # a private key header with no key after it: a mention, not a key
                continue
            start, end = m.span()
            # taken holds non-overlapping spans sorted by start: check only the neighbours
            i = bisect.bisect_left(taken, (start, end))
            if (i > 0 and taken[i - 1][1] > start) or (i < len(taken) and taken[i][0] < end):
                continue
            taken.insert(i, (start, end))
            if kind != "Private key" and PLACEHOLDER.search(value):
                continue
            if kind in NEEDS_RANDOM and not looks_random(value):
                continue
            if rx is UNQUOTED_ASSIGNMENT and IDENTIFIER.fullmatch(value):
                continue
            if kind == "Password or secret assignment" and not any(c.isdigit() for c in value):
                continue  # words and phrases (change-me, replace-with-random) are placeholders
            yield kind, value


def patterns_version():
    """Changes whenever a pattern does, so cached scan results never outlive the rules that made them."""
    text = "|".join(f"{k}={rx.pattern}" for k, rx in SECRET_PATTERNS) + PLACEHOLDER.pattern + repr(NEEDS_RANDOM)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scan_secrets(archive):
    """{fingerprint: {kind, masked, places}} for every credential under all-sessions/, re-reading only
    files whose size or mtime changed. The cache holds fingerprints, masked forms and line numbers:
    never a value."""
    root = archive / "all-sessions"
    salt = secrets_salt(archive)
    version = patterns_version() + hashlib.sha256(salt.encode()).hexdigest()[:8]
    try:
        cache = json.loads(SECRETS_CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict) or cache.get("v") != version or cache.get("root") != str(root):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("files", {}) if isinstance(cache.get("files"), dict) else {}
    new, files = {}, 0
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if path.is_symlink() or not path.is_file() or path.suffix not in SECRET_SUFFIXES:
            continue
        files += 1
        rel = path.relative_to(archive).as_posix()
        try:
            st = path.stat()
        except OSError:
            continue
        sig = [st.st_size, int(st.st_mtime)]
        hit = old.get(rel)
        if isinstance(hit, dict) and hit.get("sig") == sig and isinstance(hit.get("hits"), list):
            new[rel] = hit
            continue
        hits = []
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    for kind, value in set(find_secrets(line)):  # a line often repeats a value
                        hits.append([fingerprint(value, salt), kind, mask(value, kind), n])
        except OSError as exc:
            log(f"skip {path}: {exc}")
            continue
        new[rel] = {"sig": sig, "hits": hits}
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(SECRETS_CACHE, json.dumps({"v": version, "root": str(root), "files": new}))
    except OSError:
        pass
    ignore = read_data(archive / "data" / "secrets-ignore.json", "secrets to ignore").get("ignore", [])
    ignore = {i for i in ignore if isinstance(i, str)}
    found = {}
    for rel in sorted(new):
        for fp, kind, masked, n in new[rel]["hits"]:
            if fp in ignore:
                continue
            h = found.setdefault(fp, {"kind": kind, "masked": masked, "places": []})
            if len(h["places"]) < 1000:
                h["places"].append(f"{rel}:{n}")
    return files, found


def write_secrets_report(archive, files, found):
    order = sorted(found.items(), key=lambda kv: (kv[1]["kind"] == "Password or secret assignment", kv[1]["kind"], -len(kv[1]["places"])))
    out = [
        "# Secrets found in the archive", "",
        f"Scanned {files} files under `all-sessions/` on {datetime.now():%Y-%m-%d %H:%M}. Do not edit: rebuilt on every sync and by `claude-session-archive secrets`.", "",
        "Values are masked and never written here. A real credential below has been stored in plain text in every copy of this archive "
        "(and in `~/.claude` on the computer it came from): **rotate it** with the service that issued it. "
        "To silence one that is not a secret (a test value), add its fingerprint to `data/secrets-ignore.json` as `{\"ignore\": [\"<fingerprint>\"]}`.", "",
        f"**{len(found)} distinct value(s)** found." + (" Pattern matches are not proof: check each one." if found else ""), "",
    ]
    if found:
        out += ["| Kind | Value (masked) | Fingerprint | Occurrences | First seen in |", "|---|---|---|---|---|"]
        for fp, h in order:
            out.append(f"| {clean(h['kind'])} | `{clean(h['masked'])}` | `{fp}` | {len(h['places'])} | `{clean(h['places'][0])}` |")
        out.append("")
    write_atomic(archive / "SECRETS.md", "\n".join(out) + "\n")


def secrets_summary(archive):
    """The finding count from the last SECRETS.md, or None if there is none."""
    try:
        m = re.search(r"\*\*(\d{1,7}) distinct value", (archive / "SECRETS.md").read_text(encoding="utf-8"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def cmd_secrets(args):
    """Scan every file in the archive for credentials and write SECRETS.md, which shows each one
    masked, with a fingerprint and where it occurs. Nothing is changed in the transcripts."""
    archive = archive_dir()
    files, found = scan_secrets(archive)
    write_secrets_report(archive, files, found)
    build_index(archive)
    kinds = {}
    for h in found.values():
        kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
    print(f"{files} files scanned, {len(found)} distinct value(s) found" + (": " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) if kinds else ""))
    print(f"report: {archive / 'SECRETS.md'} (values masked)")
    return 1 if found and args.fail else 0


# ---------------------------------------------------------------- import


# One exported session; the largest seen in practice is about 10 MB. Overridable for tests.
MAX_RECORD_BYTES = int(os.environ.get("CLAUDE_SESSION_ARCHIVE_MAX_RECORD_BYTES") or 256 * 1024 * 1024)


def read_lines(fh):
    """Lines of a text stream, refusing any longer than MAX_RECORD_BYTES (yields None for those)
    so a hostile or corrupt file cannot exhaust memory."""
    while True:
        line = fh.readline(MAX_RECORD_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_RECORD_BYTES and not line.endswith("\n"):
            while True:  # discard the rest of the over-long line
                rest = fh.readline(1 << 20)
                if not rest or rest.endswith("\n"):
                    break
            yield None
            continue
        yield line


def export_records(path):
    """Session records from a claude.ai export: JSON Lines, one session per line, or one JSON
    object holding {"sessions": [...]} (on one line or indented); either may be gzip-compressed.
    Yields records, or (None, reason) for a line that could not be used."""
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    opener = gzip.open if gz else open
    with opener(path, "rt", encoding="utf-8-sig") as fh:
        first = True
        for line in read_lines(fh):
            if line is None:
                yield None, "record larger than the size limit"
                continue
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (ValueError, RecursionError):
                if first and line.lstrip().startswith("{"):  # an indented single JSON document
                    yield from whole_document(path, opener)
                    return
                yield None, "line is not JSON"
                continue
            first = False
            items = obj.get("sessions") if isinstance(obj, dict) and isinstance(obj.get("sessions"), list) else [obj]
            for item in items:
                yield item


def whole_document(path, opener):
    with opener(path, "rt", encoding="utf-8-sig") as fh:
        text = fh.read(MAX_RECORD_BYTES + 1)
    if len(text) > MAX_RECORD_BYTES:
        yield None, "file larger than the size limit"
        return
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        yield None, "not JSON"
        return
    items = obj.get("sessions") if isinstance(obj, dict) and isinstance(obj.get("sessions"), list) else [obj]
    for item in items:
        yield item


def event_keys(events):
    """Identities of a session's events, to tell a grown copy from a different one."""
    keys = set()
    for e in events:
        if isinstance(e, dict):
            k = e.get("event_id") or e.get("uuid") or e.get("sequence_num")
            keys.add(str(k) if k is not None else json.dumps(e, sort_keys=True)[:200])
    return keys


def file_record(dest, rec):
    """Write one session record. Returns new / updated / unchanged / kept-beside / skipped.
    A saved copy is only replaced by one that contains every event it has; any other different
    copy is kept beside it under versions/, never over it."""
    sid = rec.get("id") if isinstance(rec, dict) else None
    if isinstance(sid, str) and sid.startswith("cse_"):
        sid = "session_" + sid[4:]
    if not (isinstance(sid, str) and SESSION_ID.fullmatch(sid) and isinstance(rec.get("events"), list)):
        return "skipped"
    if not (isinstance(rec.get("metadata"), dict) or isinstance(rec.get("listing"), dict)):
        return "skipped"  # not what export-claude-ai.js writes
    keep = {k: rec[k] for k in ("exported_at", "listing", "metadata") if k in rec}
    body = json.dumps({"id": sid, **keep, "events": rec["events"]}, ensure_ascii=False)
    out = dest / f"{sid}.json"
    if out.is_symlink() or (out.exists() and not out.is_file()):
        log(f"skip {out}: not a regular file")
        return "skipped"
    if not out.exists():
        write_atomic(out, body)
        return "new"
    try:
        have = json.loads(out.read_text(encoding="utf-8")).get("events")
        have = have if isinstance(have, list) else None
    except (OSError, ValueError, AttributeError, RecursionError):
        have = None
    new_keys = event_keys(rec["events"])
    if have is not None:
        old_keys = event_keys(have)
        if new_keys <= old_keys:
            return "unchanged"
        if old_keys <= new_keys:
            write_atomic(out, body)
            return "updated"
    # Different content, or an existing copy that cannot be read: keep both.
    side = dest / "versions" / f"{sid}.{hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]}.json"
    if side.is_symlink() or side.exists():
        return "unchanged"
    side.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(side, body)
    return "kept beside"


def cmd_import(args):
    """File sessions exported from claude.ai into all-sessions/cloud/, one file per session.
    Never replaces a saved copy with one that has lost events; never stops on a bad file."""
    archive = archive_dir()
    if not archive.is_dir():
        raise Fail(f"archive not reachable: {archive}")
    for d in (archive / "all-sessions", archive / "all-sessions" / "cloud"):
        if d.is_symlink():
            raise Fail(f"{d} is a symlink; refusing to write through it")
    dest = archive / "all-sessions" / "cloud"
    dest.mkdir(parents=True, exist_ok=True)
    if not acquire_lock():
        raise Fail("a sync is running; try again in a minute")
    counts, problems, added = {}, [], []
    try:
        for name in args.files:
            path = Path(name).expanduser()
            if not path.is_file():
                problems.append(f"{path}: not a file")
                continue
            try:
                for rec in export_records(path):
                    if isinstance(rec, tuple):
                        problems.append(f"{path.name}: {rec[1]}")
                        continue
                    result = file_record(dest, rec)
                    counts[result] = counts.get(result, 0) + 1
                    if result == "new":
                        meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
                        listing = rec.get("listing") if isinstance(rec.get("listing"), dict) else {}
                        title = meta.get("title") or listing.get("title") or "(untitled)"
                        added.append(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(title))[:100])
            except (OSError, EOFError, UnicodeDecodeError, ValueError, gzip.BadGzipFile if hasattr(gzip, "BadGzipFile") else OSError) as exc:
                problems.append(f"{path.name}: unreadable ({type(exc).__name__}); sessions before the error were kept")
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
    print(", ".join(f"{counts.get(k, 0)} {k}" for k in ("new", "updated", "unchanged", "kept beside", "skipped")))
    for t in added[:50]:
        print(f"  + {t}")
    if len(added) > 50:
        print(f"  + ... and {len(added) - 50} more")
    for p in problems:
        print(f"problem: {p}")
    print(f"{build_index(archive)} sessions indexed")
    print("The export files hold whole conversations: delete them now that they are filed.")
    return 1 if problems else 0


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
    for p in (INSTALLED, CONF, STAMP, LOCK, CACHE, SAVED_CACHE, SECRETS_CACHE, WINDOWS_SHIM):
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
    im = sub.add_parser("import", help="file sessions exported from claude.ai into the archive")
    im.add_argument("files", nargs="+", help="export files (.jsonl, .json, optionally .gz)")
    im.set_defaults(fn=cmd_import)
    sc = sub.add_parser("secrets", help="scan the archive for credentials, report them masked")
    sc.add_argument("--fail", action="store_true", help="exit 1 if anything is found")
    sc.set_defaults(fn=cmd_secrets)
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
