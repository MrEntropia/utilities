#!/usr/bin/env python3
"""claude-session-archive: keep your Claude Code session history, back it up, and index it.

Commands:
  install [--archive DIR]   stop transcript auto-deletion, add the daily sync hook, run a first sync
  sync [--if-stale]         copy new and grown transcripts to the archive, rebuild the indexes
  index                     rebuild INDEX.md and SESSIONS.md from the archive
  digest OUTDIR             write one condensed text file per session (for summarising)
  import FILE...            file sessions exported from claude.ai into the archive
  secrets                   scan the archive for credentials; report them masked in SECRETS.md
  redact FINGERPRINT...     replace chosen credentials with a <descriptor> (dry run unless --apply)
  status                    show what is configured, and whether the hook can run
  remind                    (run by the hook) say when claude.ai sessions are due to be saved
  memory [link|forget]      share Claude Code memory notes between your computers
  setup restore             bring your Claude Code setup (skills, agents, settings) onto this computer
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

VERSION = "1.6.0"
CACHE_VERSION = 4  # bump when scan_session() returns new fields, so cached rows are re-read
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
SETTINGS_CACHE = CONF_DIR / "settings-cache.json"  # last shared settings seen, for when the drive is offline
LAST_OK = CONF_DIR / "last-ok"            # touched when a sync completes
LAST_REMINDER = CONF_DIR / "last-reminder"
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


# ---------------------------------------------------------------- shared settings

# data/settings.json in the archive: one file every computer reads. Each entry: default, minimum, maximum.
SHARED_SETTINGS = {
    "sync_every_hours": (20, 1, 720),
    "scan_secrets_on_sync": (True, None, None),
    "reminders": (True, None, None),
    "remind_claude_ai_import_every_days": (14, 1, 365),
    "remind_if_sync_older_than_days": (3, 1, 365),
}
SETTINGS_ABOUT = ("Shared by every computer that syncs this archive. sync_every_hours: how often a session start "
                  "syncs. scan_secrets_on_sync: run the secrets scan in each sync. reminders: show reminders when a "
                  "Claude Code session starts. remind_claude_ai_import_every_days: remind you to save your claude.ai "
                  "sessions (export-claude-ai.js, then import) this often. remind_if_sync_older_than_days: remind you "
                  "if this computer has not synced for this long.")


def shared_settings(archive=None):
    """The shared settings, each checked against its type and range; anything missing, wrong or
    unreadable falls back to its default. Read from the archive when given and reachable (and
    remembered locally), otherwise from the last copy seen."""
    raw = None
    if archive is not None:
        try:
            raw = json.loads((archive / "data" / "settings.json").read_text(encoding="utf-8-sig"))
            write_atomic(SETTINGS_CACHE, json.dumps(raw))
        except (OSError, ValueError):
            raw = None
    if raw is None:
        try:
            raw = json.loads(SETTINGS_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    out = {}
    for key, (default, lo, hi) in SHARED_SETTINGS.items():
        v = raw.get(key, default) if isinstance(raw, dict) else default
        if isinstance(default, bool):
            out[key] = v if isinstance(v, bool) else default
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = v
        else:
            out[key] = default
    return out


def ensure_shared_settings(archive):
    """Create data/settings.json with the defaults if it does not exist yet (never overwrites)."""
    path = archive / "data" / "settings.json"
    if not path.exists():
        body = {"about": SETTINGS_ABOUT, **{k: v[0] for k, v in SHARED_SETTINGS.items()}}
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(body, indent=2) + "\n")


def days_since(ts):
    return (time.time() - ts) / 86400


def last_import(archive):
    """(epoch seconds, computer) of the last claude.ai import into this archive, or None."""
    try:
        rec = json.loads((archive / "data" / "claude-ai-import.json").read_text(encoding="utf-8"))
        return datetime.fromisoformat(rec["last"]).timestamp(), str(rec.get("computer", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- memory
#
# Each computer mirrors its own memory notes, as they are now, to
# all-sessions/memory/<computer>/<project folder>/ (only that computer writes there, so the mirror
# can follow edits and deletions; the append-only copy under all-sessions/projects/ keeps history).
# Every sync then brings here, for each project this computer has, the notes another computer has
# and this one does not. A note that exists here is never written over or through; a note this
# computer deleted is not brought back, and is recorded (by content) so other computers do not
# bring it back either.

MEMORY_GROUPS = "memory-projects.json"   # data/: projects linked across computers with different paths
FORGOTTEN_DIR = "memory-forgotten"       # data/memory-forgotten/<computer>.json: deleted notes, by content
MEMORY_SEEN = CONF_DIR / "memory-seen.json"
MAX_AUTO_FORGET = 5                      # more deletions than this in one project and one run look like an accident
NOTE_PART = re.compile(r"[A-Za-z0-9._ -]{1,200}")
MAX_INDEX_LINE = 300


def term_safe(text, cap=200):
    """Text from the archive, safe to print: no control characters (no terminal escapes)."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", str(text))[:cap]


def computer_key():
    """This computer's folder name in the archive: its name plus a random id made once at
    install, so a computer keeps one identity even when its network name changes."""
    conf = load_config()
    cid = conf.get("computer_id") if isinstance(conf.get("computer_id"), str) and re.fullmatch(r"[0-9a-f]{8}", conf.get("computer_id", "")) else ""
    name = conf.get("computer_name") if isinstance(conf.get("computer_name"), str) and conf.get("computer_name") else computer_name()
    return f"{safe_name(name)[:48]}-{cid}" if cid else safe_name(computer_name())


def claude_slug(path):
    """The folder name Claude Code gives a project: its path with every UTF-16 unit other than a
    letter, digit or hyphen turned into a hyphen (Claude Code counts an emoji as two)."""
    text = str(path)
    units = text.encode("utf-16-le")
    out = []
    for i in range(0, len(units), 2):
        ch = units[i:i + 2].decode("utf-16-le", errors="ignore") if units[i + 1] < 0xD8 or units[i + 1] > 0xDF else ""
        out.append(ch if ch and re.fullmatch(r"[A-Za-z0-9-]", ch) else "-")
    return "".join(out)


def local_project_folder(path):
    """This computer's ~/.claude/projects folder for a project path: the one whose transcripts
    record that working directory (exact), else the computed name when it is short enough."""
    target = str(Path(path).resolve())
    for folder in sorted(PROJECTS.iterdir()) if PROJECTS.is_dir() else []:
        if folder.is_dir() and not folder.is_symlink() and project_cwd(folder) == target:
            return folder.name
    slug = claude_slug(target)
    if len(slug) > 200:
        raise Fail(f"{target} is too long a path to work out its Claude Code folder; start a Claude Code "
                   "session in it once, then run this again")
    return slug


def project_cwd(folder):
    """The working directory a project folder's transcripts record, or ""."""
    for path in sorted(folder.glob("*.jsonl"))[:5]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for _ in range(50):
                    line = fh.readline(1 << 20)
                    if not line:
                        break
                    try:
                        cwd = json.loads(line).get("cwd")
                    except (ValueError, AttributeError, RecursionError):
                        continue
                    if isinstance(cwd, str) and cwd:
                        return cwd
        except OSError:
            continue
    return ""


def memory_note_files(folder):
    """{relative path: file} of the notes in a memory folder; None if the folder itself is a
    symlink or cannot be read (unknown, which is not the same as empty)."""
    if folder.is_symlink():
        return None
    if not folder.exists():
        return {}
    out = {}
    try:
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            if all(NOTE_PART.fullmatch(p) and p not in (".", "..") for p in rel.split("/")):
                out[rel] = path
    except OSError:
        return None
    return out


def memory_mirror(archive, key):
    """Mirror this computer's memory notes, as they are now, to all-sessions/memory/<computer>/."""
    root = archive / "all-sessions" / "memory"
    for d in (archive / "all-sessions", root, root / key):
        if d.is_symlink():
            raise Fail(f"{d} is a symlink; not writing through it")
    dest_root = root / key
    for folder in sorted(PROJECTS.iterdir()) if PROJECTS.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            continue
        notes = memory_note_files(folder / "memory")
        if notes is None:
            continue  # unknown: leave the previous mirror as it is
        dest = dest_root / folder.name
        mirrored = memory_note_files(dest) or {}
        for rel, src in notes.items():
            d = dest / rel
            try:
                if rel in mirrored and d.read_bytes() == src.read_bytes():
                    continue
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, d)
            except OSError as exc:
                log(f"memory mirror: skip {rel}: {type(exc).__name__}")
        for rel, d in mirrored.items():
            if rel not in notes and rel != ".project.json":
                try:
                    d.unlink()  # this computer's own mirror follows deletions; history stays in projects/
                except OSError:
                    pass
        if notes:
            write_atomic(dest / ".project.json", json.dumps({"cwd": project_cwd(folder)}) + "\n")


def memory_groups(archive):
    """{group id: {"label": name, "folders": [project folder names]}} from `memory link`."""
    raw = read_data(archive / "data" / MEMORY_GROUPS, "memory projects").get("groups", {})
    out = {}
    for gid, g in raw.items() if isinstance(raw, dict) else []:
        if not (isinstance(gid, str) and re.fullmatch(r"[0-9a-f]{8}", gid) and isinstance(g, dict)):
            continue
        folders = [x for x in g.get("folders", []) if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9-]{1,300}", x)] \
            if isinstance(g.get("folders"), list) else []
        out[gid] = {"label": term_safe(g.get("label") or gid, 80), "folders": folders}
    return out


def save_groups(archive, groups):
    write_atomic(archive / "data" / MEMORY_GROUPS, json.dumps({"groups": groups}, indent=2) + "\n")


def same_project(folder, groups):
    """The project folder names that are the same project as `folder` (itself included)."""
    for g in groups.values():
        if folder in g["folders"]:
            return set(g["folders"])
    return {folder}


def forgotten_notes(archive):
    """{(note name, content sha256)} deleted on purpose, from every computer's file."""
    out = set()
    d = archive / "data" / FORGOTTEN_DIR
    for f in sorted(d.glob("*.json")) if d.is_dir() and not d.is_symlink() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8")).get("forgotten", [])
        except (OSError, ValueError, AttributeError, RecursionError):
            continue
        for r in rec if isinstance(rec, list) else []:
            if isinstance(r, dict) and isinstance(r.get("note"), str) and isinstance(r.get("sha256"), str):
                out.add((r["note"], r["sha256"]))
    return out


def record_forgotten(archive, key, entries):
    """Add (note name, sha256, project) entries to this computer's own forgotten file."""
    path = archive / "data" / FORGOTTEN_DIR / f"{key}.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8")).get("forgotten", [])
        rec = rec if isinstance(rec, list) else []
    except (OSError, ValueError, AttributeError, RecursionError):
        rec = []
    have = {(r.get("note"), r.get("sha256")) for r in rec if isinstance(r, dict)}
    for note, sha, project in entries:
        if (note, sha) not in have:
            rec.append({"note": note, "sha256": sha, "project": project, "date": f"{datetime.now():%Y-%m-%d}"})
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"forgotten": rec}, indent=1) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def other_copies(archive, key, folders):
    """[(computer, note files)] of every other computer's mirror of these project folders."""
    root = archive / "all-sessions" / "memory"
    out = []
    for comp in sorted(root.iterdir()) if root.is_dir() and not root.is_symlink() else []:
        if comp.name == key or not comp.is_dir() or comp.is_symlink():
            continue
        for f in sorted(folders):
            notes = memory_note_files(comp / f)
            if notes:
                out.append((comp.name, notes))
    return out


def load_seen():
    """(set of "<folder>/<note>" this computer has had, whether the record was usable)."""
    try:
        data = json.loads(MEMORY_SEEN.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {x for x in data if isinstance(x, str)}, True
    except (OSError, ValueError, RecursionError):
        pass
    return set(), False


def merge_memory_index(local_text, other_text, have):
    """MEMORY.md with pointer lines from another computer's copy added, when they point to a note
    this computer has and does not list yet: capped in length, control characters removed,
    inserted after the last existing pointer, in the file's own line ending."""
    nl = "\r\n" if "\r\n" in local_text else "\n"
    lines = local_text.splitlines()
    listed = {m.group(1) for m in (POINTER.match(l) for l in lines) if m}
    extra = []
    for line in other_text.splitlines():
        m = POINTER.match(line)
        if m and m.group(1) not in listed and m.group(1) in have and len(line) <= MAX_INDEX_LINE:
            extra.append(re.sub(r"[\x00-\x1f\x7f-\x9f]", "", line))
            listed.add(m.group(1))
    if not extra:
        return local_text
    last = max((i for i, l in enumerate(lines) if POINTER.match(l)), default=len(lines) - 1)
    lines[last + 1:last + 1] = extra
    return nl.join(lines) + nl


POINTER = re.compile(r"^- \[[^\]\n]{1,300}\]\(([^)\s]{1,300})\)")


def memory_disabled_reason():
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        settings = {}
    if isinstance(settings, dict) and settings.get("autoMemoryDirectory"):
        return "autoMemoryDirectory is set in settings.json"
    if os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR"):
        return "CLAUDE_CODE_REMOTE_MEMORY_DIR is set"
    return ""


def memory_pull(archive, apply=True):
    """Bring here, for every project this computer has, the notes other computers have and this
    one does not. Returns {"pulled", "differ", "forgotten", "skipped"} (lists of strings)."""
    report = {"pulled": [], "differ": [], "forgotten": [], "skipped": []}
    reason = memory_disabled_reason()
    if reason:
        report["skipped"].append(f"memory sharing is off: {reason} (Claude Code keeps memory elsewhere)")
        return report
    if not PROJECTS.is_dir():
        return report
    key = computer_key()
    groups = memory_groups(archive)
    forgotten = forgotten_notes(archive)
    seen, seen_ok = load_seen()
    first_run = not seen_ok
    if not seen_ok and MEMORY_SEEN.exists():
        log("memory: this computer's record of its notes was unreadable; treated as a first run")
    home_folders = {claude_slug(HOME), claude_slug(HOME.resolve()), "-"}
    new_forgotten = []
    for folder in sorted(PROJECTS.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        slug = folder.name
        folders = same_project(slug, groups)
        if len(folders) == 1 and slug in home_folders:
            continue  # the home folder (or /) is never shared without an explicit link
        target = folder / "memory"
        have = memory_note_files(target)
        if have is None:
            report["skipped"].append(f"{slug}: memory folder is a symlink or unreadable; left alone")
            continue
        have_lower = {r.lower() for r in have}
        for rel in have:
            seen.add(f"{slug}/{rel}")
        if first_run and target.exists():
            # Notes this computer deleted before memory sharing existed are still in the
            # append-only copy of its own folder: they are not new, so they are not brought back.
            old = memory_note_files(archive / "all-sessions" / "projects" / slug / "memory") or {}
            seen.update(f"{slug}/{rel}" for rel in old)
        copies = other_copies(archive, key, folders)
        candidates = {}
        for comp, notes in copies:
            for rel, path in notes.items():
                if rel in (".project.json", "MEMORY.md"):
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if rel not in candidates or mtime > candidates[rel][1]:
                    candidates[rel] = (path, mtime, comp)
        deleted_here = 0
        for rel, (path, _m, comp) in sorted(candidates.items()):
            try:
                digest = sha(path)
            except OSError:
                continue
            if (rel, digest) in forgotten:
                continue
            dest = target / rel
            if rel in have or rel.lower() in have_lower or os.path.lexists(dest):
                try:
                    if rel in have and dest.read_bytes() != path.read_bytes():
                        report["differ"].append(f"{slug}/{rel} (differs from {comp})")
                except OSError:
                    pass
                continue
            if f"{slug}/{rel}" in seen:
                deleted_here += 1
                if deleted_here <= MAX_AUTO_FORGET:
                    new_forgotten.append((rel, digest, slug))
                    report["forgotten"].append(f"{slug}/{rel}")
                continue
            if apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            seen.add(f"{slug}/{rel}")
            have[rel] = dest
            have_lower.add(rel.lower())
            report["pulled"].append(f"{slug}/{rel} (from {comp})")
        if deleted_here > MAX_AUTO_FORGET:
            report["skipped"].append(f"{slug}: {deleted_here} notes are missing here that this computer had; that many "
                                     "looks like an accident, so only the first few were recorded as deleted. "
                                     "Use `memory forget` for the rest if you meant it.")
        indexes = [notes.get("MEMORY.md") for _c, notes in copies if notes.get("MEMORY.md")]
        idx = target / "MEMORY.md"
        if indexes and (idx.exists() and not idx.is_symlink() or not os.path.lexists(idx)):
            try:
                with open(idx, encoding="utf-8", newline="") as fh:
                    local = fh.read()
            except FileNotFoundError:
                local = "# Memory Index\n"
            except (OSError, UnicodeDecodeError):
                local = None
            if local is not None:
                merged = local
                for other in indexes:
                    try:
                        merged = merge_memory_index(merged, other.read_text(encoding="utf-8"), set(have))
                    except (OSError, UnicodeDecodeError):
                        pass
                if merged != local and apply:
                    target.mkdir(parents=True, exist_ok=True)
                    write_atomic(idx, merged)
    if apply:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(MEMORY_SEEN, json.dumps(sorted(seen)))
        if new_forgotten:
            record_forgotten(archive, key, new_forgotten)
    return report


def archived_projects(archive):
    """{project folder name: label} of every project folder any computer mirrored memory for."""
    out = {}
    root = archive / "all-sessions" / "memory"
    for comp in sorted(root.iterdir()) if root.is_dir() and not root.is_symlink() else []:
        for f in sorted(comp.iterdir()) if comp.is_dir() and not comp.is_symlink() else []:
            if f.is_dir() and not f.is_symlink() and f.name not in out:
                try:
                    cwd = json.loads((f / ".project.json").read_text(encoding="utf-8")).get("cwd", "")
                except (OSError, ValueError, AttributeError, RecursionError):
                    cwd = ""
                home = claude_slug(HOME) + "-"
                fallback = f.name[len(home):] if f.name.startswith(home) and len(f.name) > len(home) else f.name
                out[f.name] = term_safe(os.path.basename(str(cwd).replace("\\", "/").rstrip("/")) or fallback, 80)
    return out


def resolve_project(archive, query, exclude=()):
    """The group id or project folder a name refers to: a linked project's name, a project's own
    name (as `memory` lists it), or a project folder name."""
    groups = memory_groups(archive)
    by_label = [gid for gid, g in groups.items() if g["label"] == query]
    if len(by_label) == 1:
        return by_label[0]
    projects = {f: l for f, l in archived_projects(archive).items() if f not in exclude}
    hits = {f for f, l in projects.items() if l == query or f == query}
    resolved = set()
    for f in hits:
        gid = next((g for g, v in groups.items() if f in v["folders"]), None)
        resolved.add(gid or f)
    if len(resolved) == 1:
        return resolved.pop()
    raise Fail(f"no single project matches {term_safe(query)!r}" + (f" ({len(resolved)} do)" if resolved else "")
               + "; `claude-session-archive memory` lists the names")


def cmd_memory(args):
    archive = archive_dir()
    rest = list(args.args or [])
    if args.action == "link":
        if len(rest) != 2:
            raise Fail("usage: memory link <project> <its folder on this computer> [--apply]")
        local = Path(rest[1]).expanduser().resolve()
        if not local.is_dir():
            raise Fail(f"{local} is not a folder")
        slug = local_project_folder(local)
        groups = memory_groups(archive)
        target = resolve_project(archive, rest[0], exclude={slug})
        folders = set(groups[target]["folders"]) if target in groups else {target}
        if slug in folders:
            print(f"{local} already shares memory with {term_safe(rest[0])}")
            return 0
        if any(slug in g["folders"] for g in groups.values()):
            raise Fail(f"{local} is already linked to another project; `memory unlink` it first")
        local_twins = [f for f in folders if (PROJECTS / f).is_dir()]
        if local_twins and not args.force:
            raise Fail(f"{term_safe(rest[0])} is also a project on this computer ({', '.join(local_twins)}); linking would "
                       "merge two of your projects here. Add --force if that is what you want.")
        label = groups[target]["label"] if target in groups else term_safe(rest[0], 80)
        if target not in groups and any(g["label"] == label for g in groups.values()):
            label = f"{label} ({len(groups) + 1})"
        notes = sum(len(n) for _c, n in other_copies(archive, computer_key(), folders))
        print(f"Link {local} to {label}: {notes} note file(s) from your other computers would be brought here "
              "(only ones this computer lacks; nothing here is changed).")
        if not args.apply:
            print("Dry run: nothing changed. Add --apply to link.")
            return 0
        gid = target if target in groups else random_bytes.token_hex(4)
        groups[gid] = {"label": label, "folders": sorted(folders | {slug})}
        save_groups(archive, groups)
        (PROJECTS / slug / "memory").mkdir(parents=True, exist_ok=True)
        r = memory_pull(archive)
        print(f"linked; {len(r['pulled'])} note(s) brought here")
        return 0
    if args.action == "unlink":
        if len(rest) != 1:
            raise Fail("usage: memory unlink <folder of the project on this computer>")
        slug = local_project_folder(Path(rest[0]).expanduser().resolve())
        groups = memory_groups(archive)
        for gid, g in groups.items():
            if slug in g["folders"]:
                g["folders"] = [f for f in g["folders"] if f != slug]
                if len(g["folders"]) < 2:
                    del groups[gid]
                save_groups(archive, groups)
                print(f"unlinked {term_safe(rest[0])}; notes already brought here stay")
                return 0
        raise Fail("that folder is not linked to anything")
    if args.action == "forget":
        if len(rest) != 2:
            raise Fail("usage: memory forget <project> <note file>")
        rel = rest[1].strip("/")
        if rel == "MEMORY.md" or not all(NOTE_PART.fullmatch(p) and p not in (".", "..") for p in rel.split("/")):
            raise Fail(f"not a note you can forget: {term_safe(rel)!r}")
        target = resolve_project(archive, rest[0])
        groups = memory_groups(archive)
        folders = set(groups[target]["folders"]) if target in groups else {target}
        entries, removed = [], 0
        for f in folders:
            local = PROJECTS / f / "memory" / rel
            if local.is_file() and not local.is_symlink():
                entries.append((rel, sha(local), f))
                local.unlink()
                removed += 1
        for _comp, notes in other_copies(archive, computer_key(), folders):
            if rel in notes:
                entries.append((rel, sha(notes[rel]), "elsewhere"))
        if not entries:
            raise Fail(f"no note {term_safe(rel)!r} found for that project")
        record_forgotten(archive, computer_key(), entries)
        print(f"forgot {term_safe(rel)}: removed here ({removed} file(s)); no computer will bring this version back. "
              "The archive keeps its history, as it keeps everything. Remove its line from MEMORY.md if it has one.")
        return 0
    # status
    r = memory_pull(archive, apply=False)
    projects = archived_projects(archive)
    groups = memory_groups(archive)
    print("Projects with memory notes on your computers:")
    shown = set()
    for f, label in sorted(projects.items(), key=lambda kv: kv[1].lower()):
        folders = same_project(f, groups)
        if f in shown:
            continue
        shown |= folders
        name = next((g["label"] for g in groups.values() if f in g["folders"]), label)
        here = any((PROJECTS / x).is_dir() for x in folders)
        hint = "here" if here else f"not here; to bring them: claude-session-archive memory link {shlex.quote(name)} <its folder> --apply"
        print(f"  {name}: {hint}")
    print(f"would bring here: {len(r['pulled'])} note(s); notes that differ from another computer's copy (kept as they are): {len(r['differ'])}")
    for line in (r["differ"] + r["skipped"])[:30]:
        print(f"  {term_safe(line, 300)}")
    return 0


# ---------------------------------------------------------------- setup backup and restore

# What makes up your Claude Code setup, relative to ~/.claude. Caches, transcripts, history and
# credentials are not here: transcripts are archived separately, and you sign in again.
SETUP_ITEMS = ["CLAUDE.md", "settings.json", "keybindings.json", "skills", "skills-disabled", "agents",
               "agents-disabled", "commands", "hooks", "output-styles", "plugins/installed_plugins.json",
               "plugins/known_marketplaces.json"]
SETUP_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
SETUP_MAX_FILE = 5 * 1024 * 1024
SETUP_HISTORY_KEEP = 10  # dated versions kept per file

# Settings that run programs, send data somewhere, or switch safeguards off. Hooks and the commands
# in COMMAND_SETTINGS are restored only with --with-hooks, each shown in full first; the rest are
# never restored automatically: they are listed, and you copy any you trust by hand.
COMMAND_SETTINGS = ("statusLine", "fileSuggestion")
NEVER_AUTO = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh", "otelHeadersHelper",
              "proxyAuthHelper", "disableAllHooks", "enableAllProjectMcpServers", "enabledMcpjsonServers",
              "autoMemoryDirectory", "allowedHttpHookUrls", "forceLoginMethod", "forceLoginOrgUUID")
RISKY_ENV = re.compile(r"^(?:ANTHROPIC_.*|.*_PROXY|NODE_OPTIONS|NODE_PATH|LD_.*|DYLD_.*|PATH|PYTHON.*|CLAUDE_CODE_.*|"
                       r"CLAUDE_CONFIG_DIR|HOME|SHELL|.*_BASE_URL|.*_ENDPOINT)$", re.I)


def setup_files(home_claude):
    """{relative path: file} of the setup files under ~/.claude worth keeping."""
    out = {}
    for item in SETUP_ITEMS:
        base = home_claude / item
        if base.is_symlink():
            continue
        paths = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for path in paths:
            rel = path.relative_to(home_claude)
            if path.is_symlink() or not path.is_file() or set(rel.parts) & SETUP_SKIP_DIRS:
                continue
            try:
                if path.stat().st_size > SETUP_MAX_FILE:
                    continue
            except OSError:
                continue
            out[rel.as_posix()] = path
    return out


def is_setup_path(rel):
    """True for a path that belongs to one of SETUP_ITEMS (and nothing else, such as credentials)."""
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return any(rel == item or rel.startswith(item + "/") for item in SETUP_ITEMS) and not (set(parts) & SETUP_SKIP_DIRS)


def setup_backup(archive, key):
    """Copy this computer's setup to setup/<computer>/files/. A changed file is replaced and its
    previous version kept under setup/<computer>/history/<date>/ (the newest 10 per file)."""
    root = archive / "setup"
    dest = root / key
    for d in (root, dest, dest / "files", dest / "history"):
        if d.is_symlink():
            raise Fail(f"{d} is a symlink; not writing through it")
    stamp = f"{datetime.now():%Y-%m-%d}"
    written = []
    for rel, src in setup_files(CLAUDE_DIR).items():
        d = dest / "files" / rel
        try:
            if d.is_symlink():
                continue
            if d.exists():
                if d.read_bytes() == src.read_bytes():
                    continue
                old = dest / "history" / stamp / rel
                if not os.path.lexists(old):
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(d, old)
                    written.append(old)
                    prune_setup_history(dest / "history", rel)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, d)
            written.append(d)
        except OSError as exc:
            log(f"setup: skip {rel}: {type(exc).__name__}")
    write_atomic(dest / "about.json", json.dumps({"computer": computer_name(), "key": key, "home": str(HOME),
                                                   "claude_dir": str(CLAUDE_DIR), "os": sys.platform,
                                                   "saved": datetime.now().isoformat(timespec="seconds")}, indent=2) + "\n")
    return written


def prune_setup_history(history, rel):
    dates = sorted(d for d in (p.name for p in history.iterdir()) if DATE.fullmatch(d) and (history / d / rel).exists()) \
        if history.is_dir() else []
    for old in dates[:-SETUP_HISTORY_KEEP]:
        try:
            (history / old / rel).unlink()
        except OSError:
            pass


RESTORE_EXPLANATION = """\
What `setup restore` does

It brings the Claude Code setup you had on another computer onto this one. Each sync saves
each computer's setup to setup/<computer>/ in your archive: skills, agents, slash commands,
hook scripts, output styles, CLAUDE.md, keybindings and settings.

  * Files you do not have here are copied into ~/.claude. A file you already have is never
    overwritten; if the two differ, it is listed so you can compare them yourself.
  * settings.json is merged, not copied. Settings this computer does not have yet are added
    (permission rules, environment variables, enabled plugins, and so on), and paths in the
    other computer's home folder are rewritten to yours. A setting you already have is kept.
    Every permission rule added is shown. Environment variables are listed by name only.
    Your current settings.json is backed up next to it first.
  * Settings that run programs on this computer (hooks, the status line) are added only if
    you also give --with-hooks, and every command is shown in full first, so you can check it.
  * Settings that change where your data goes or switch safeguards off (API key helpers,
    permission mode, API base URL and proxy variables, MCP auto-approval, and the like) are
    never restored automatically: they are listed, for you to copy by hand if you trust them.
  * Nothing is deleted.

What it does not do

  * Sign you in: run `claude` and log in on this computer.
  * Install plugins: it lists the ones the other computer had; add them with /plugin.
  * Bring memory notes: those follow your projects (see `claude-session-archive memory`).

Only restore from your own archive: whoever can write to that folder decides what this copies.
Without --apply this is a dry run: it shows what would change and changes nothing.
"""


def setup_sources(archive):
    root = archive / "setup"
    out = {}
    if not root.is_dir() or root.is_symlink():
        return out
    for d in sorted(root.iterdir()):
        try:
            about = json.loads((d / "about.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(about, dict) and not d.is_symlink() and (d / "files").is_dir() and not (d / "files").is_symlink():
            out[d.name] = (d, about)
    return out


def rewrite_home(value, old_home, new_home):
    """Every string in a settings value with the other computer's home folder, as a whole path
    component, replaced by this one's (/Users/al is not a prefix of /Users/alice)."""
    if isinstance(value, str):
        if not old_home or old_home == new_home:
            return value
        return re.sub(re.escape(old_home) + r"(?=[/\\]|$|[\s\"'])", lambda _m: new_home, value)
    if isinstance(value, list):
        return [rewrite_home(v, old_home, new_home) for v in value]
    if isinstance(value, dict):
        return {k: rewrite_home(v, old_home, new_home) for k, v in value.items()}
    return value


def show(value, cap=300):
    return term_safe(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value, cap)


def hook_commands(entry):
    out = []
    for h in entry.get("hooks", []) if isinstance(entry, dict) and isinstance(entry.get("hooks"), list) else []:
        if isinstance(h, dict):
            what = h.get("command") or h.get("url") or h.get("prompt") or ""
            args = " ".join(a for a in h.get("args", []) if isinstance(a, str)) if isinstance(h.get("args"), list) else ""
            out.append(f"{h.get('type', 'command')}: {what} {args}".strip())
    return out


def merge_settings(local, other, old_home, new_home, with_hooks=False):
    """(merged settings, notes, runs, held). Adds only what `local` lacks. `runs` lists what would
    run programs (added only with with_hooks); `held` lists settings never restored automatically."""
    other = rewrite_home(other, old_home, new_home)
    merged = json.loads(json.dumps(local))
    notes, runs, held = [], [], []
    for key, value in other.items():
        if key == "hooks":
            continue
        if key in NEVER_AUTO:
            if key not in merged:
                held.append(f"{key} = {show(value)}")
            continue
        if key in COMMAND_SETTINGS:
            if key not in merged:
                runs.append(f"{key}: {show(value)}")
                if with_hooks:
                    merged[key] = value
            continue
        if key == "permissions" and isinstance(value, dict):
            mine = merged.setdefault("permissions", {})
            if not isinstance(mine, dict):
                continue
            for k, v in value.items():
                if k == "defaultMode":
                    if k not in mine:
                        held.append(f"permissions.defaultMode = {show(v)}")
                    continue
                if isinstance(v, list) and isinstance(mine.get(k, []), list):
                    extra = [x for x in v if x not in mine.get(k, [])]
                    if extra:
                        mine[k] = list(mine.get(k, [])) + extra
                        notes += [f"permissions.{k} + {show(x, 200)}" for x in extra]
                elif k not in mine:
                    mine[k] = v
                    notes.append(f"permissions.{k} = {show(v)}")
            continue
        if key == "env" and isinstance(value, dict):
            mine = merged.setdefault("env", {})
            if not isinstance(mine, dict):
                continue
            for k, v in value.items():
                if k in mine:
                    continue
                if RISKY_ENV.match(k):
                    held.append(f"env.{k} (changes how programs run or where requests go; value: {show(v, 120)})")
                    continue
                mine[k] = v
                notes.append(f"env.{k} added (value not shown)")
            continue
        if key not in merged:
            merged[key] = value
            notes.append(f"{key} = {show(value, 160)}")
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            for k in [k for k in value if k not in merged[key]]:
                merged[key][k] = value[k]
                notes.append(f"{key}.{k} = {show(value[k], 160)}")
    hooks = other.get("hooks") if isinstance(other.get("hooks"), dict) else {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        current = merged.get("hooks") if isinstance(merged.get("hooks"), dict) else {}
        mine = current.get(event) if isinstance(current.get(event), list) else []
        for entry in entries:
            if is_ours(entry) or entry in mine:
                continue
            for c in hook_commands(entry) or ["(empty hook)"]:
                runs.append(f"hook {event}: {term_safe(c, 400)}")
            if with_hooks:
                merged.setdefault("hooks", {}).setdefault(event, []).append(entry)
                mine = merged["hooks"][event]
    return merged, notes, runs, held


def cmd_setup(args):
    if args.action != "restore":
        raise Fail("usage: setup restore [--from COMPUTER] [--with-hooks] [--apply]")
    print(RESTORE_EXPLANATION)
    archive = archive_dir()
    sources = setup_sources(archive)
    here = computer_key()
    if args.source:
        if args.source not in sources:
            raise Fail(f"no setup saved from {term_safe(args.source)!r}; saved: {', '.join(map(term_safe, sources)) or 'none yet'}")
        name = args.source
    else:
        others = [n for n in sources if n != here]
        if len(others) != 1:
            listing = "\n  ".join(f"{term_safe(n)} ({term_safe(sources[n][1].get('computer', '?'))}, saved {term_safe(sources[n][1].get('saved', '?'))})"
                                   for n in sources) or "(none yet: run a sync on the other computer first)"
            raise Fail("choose the computer to restore from with --from:\n  " + listing)
        name = others[0]
    folder, about = sources[name]
    old_home = about.get("home") if isinstance(about.get("home"), str) else ""
    print(f"From: {term_safe(about.get('computer', name))} ({term_safe(name)}), saved {term_safe(about.get('saved', '?'))}; "
          f"home folder {term_safe(old_home or '?')} -> {HOME}\n")
    # Settings first: if this computer's settings.json cannot be merged, nothing is copied at all.
    other = None
    other_path = folder / "files" / "settings.json"
    if other_path.is_file() and not other_path.is_symlink():
        try:
            other = json.loads(other_path.read_text(encoding="utf-8-sig"))
            if not isinstance(other, dict):
                raise ValueError("not an object")
        except (ValueError, RecursionError) as exc:
            print(f"settings.json from that computer could not be read ({type(exc).__name__}); settings left out")
            other = None
    local, raw = read_settings()
    merged, notes, runs, held = merge_settings(local, other or {}, old_home, str(HOME), args.with_hooks)
    files = folder / "files"
    copied, differ, failed, ignored = [], [], [], 0
    for path in sorted(files.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(files).as_posix()
        if rel == "settings.json":
            continue
        if not is_setup_path(rel):
            ignored += 1
            continue
        dest = CLAUDE_DIR / rel
        if os.path.lexists(dest):
            try:
                if not dest.is_file() or dest.is_symlink() or dest.read_bytes() != path.read_bytes():
                    differ.append(rel)
            except OSError:
                differ.append(rel)
            continue
        if args.apply:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(dest):
                    differ.append(rel)
                    continue
                shutil.copy2(path, dest)
            except OSError as exc:
                failed.append(f"{rel} ({type(exc).__name__})")
                continue
        copied.append(rel)
    groups = {}
    for rel in copied:
        groups.setdefault(rel.split("/")[0], []).append(rel)
    print(("Copied" if args.apply else "Would copy") + f": {len(copied)} file(s)")
    for top, rels in sorted(groups.items()):
        print(f"  {term_safe(top)}: {len(rels)}")
    if differ:
        print(f"Kept yours (they differ from the other computer's): {len(differ)} file(s)")
        for rel in differ[:30]:
            print(f"  {term_safe(rel)}")
    if ignored:
        print(f"Ignored {ignored} file(s) that are not part of a Claude Code setup (credentials, caches and the like are never restored)")
    for f in failed:
        print(f"Could not copy: {term_safe(f)}")
    print(("Settings added" if args.apply else "Settings that would be added") + f": {len(notes) or 'none'}")
    for n in notes:
        print(f"  {n}")
    if runs:
        print("\nThese run programs on this computer (" + ("added" if args.with_hooks else "not added; give --with-hooks to add them after checking each one") + "):")
        for r in runs:
            print(f"  {r}")
    if held:
        print("\nNot restored (they change where data goes or switch safeguards off; copy any you trust by hand):")
        for h in held:
            print(f"  {h}")
    if args.apply and (notes or (runs and args.with_hooks)):
        write_settings(merged, raw)
    try:
        enabled = (other or {}).get("enabledPlugins", {})
        plugins = sorted(k for k, v in enabled.items() if v) if isinstance(enabled, dict) else []
    except AttributeError:
        plugins = []
    if plugins:
        print(f"\nPlugins that computer had enabled (install any you need with /plugin): {', '.join(map(term_safe, plugins))}")
    if not args.apply:
        print("\nDry run: nothing was changed. Run again with --apply to do it.")
    else:
        print("\n" + ("Done, with problems listed above. " if failed else "Done. ")
              + "Start a new Claude Code session to pick up the new skills, agents and settings.")
    return 1 if failed else 0


# ---------------------------------------------------------------- sync


def copy_additive(src, archive_root, host, written=None):
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
                if written is not None:
                    written.append(d)
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


def computer_name():
    """This computer's name (CLAUDE_SESSION_ARCHIVE_HOSTNAME overrides it, for tests)."""
    return os.environ.get("CLAUDE_SESSION_ARCHIVE_HOSTNAME") or socket.gethostname()


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
    every = shared_settings()["sync_every_hours"] * 3600  # last copy seen: no drive access needed to decide
    if args.if_stale and STAMP.exists() and time.time() - STAMP.stat().st_mtime < every:
        return 0
    if not archive.is_dir():
        log(f"archive not reachable (cloud drive not mounted?): {archive}")
        return 1
    if not acquire_lock():
        log("another sync is running; skipped")
        return 0
    try:
        STAMP.touch()  # at the start, so session starts during a long sync do not pile up
        host = computer_name()
        copied, written = 0, []
        if PROJECTS.is_dir():
            copied = copy_additive(PROJECTS, archive / "all-sessions", host, written)
            write_computer_manifest(PROJECTS, archive / "all-sessions", host)
        try:  # what you redacted stays redacted, whatever this computer's transcripts still hold
            enforce_redactions(archive, written)
        except (Fail, OSError, ValueError) as exc:
            log(f"redactions not re-applied: {exc}")
        key = computer_key()
        try:  # memory notes your other computers have, for projects this computer has
            pulled = memory_pull(archive)
            memory_mirror(archive, key)
            for line in pulled["skipped"]:
                log(f"memory: {line}")
            if pulled["pulled"] or pulled["differ"] or pulled["forgotten"]:
                log(f"memory: {len(pulled['pulled'])} brought here, {len(pulled['differ'])} differ, "
                    f"{len(pulled['forgotten'])} recorded as deleted here")
        except (Fail, OSError, ValueError, RecursionError) as exc:
            log(f"memory not shared: {exc}")
        try:
            enforce_redactions(archive, setup_backup(archive, key))
        except (Fail, OSError, ValueError, RecursionError) as exc:
            log(f"setup not saved: {exc}")
        settings = shared_settings(archive)
        try:  # a failed scan must never stop the archive from being kept
            if settings["scan_secrets_on_sync"]:
                write_secrets_report(archive, *scan_secrets(archive))
        except (Fail, OSError, ValueError) as exc:
            log(f"secrets scan skipped: {exc}")
        count = build_index(archive)
        write_atomic(archive / "LAST-SYNC.txt", f"{datetime.now():%Y-%m-%d %H:%M} {host}\n")
        log(f"ok: {copied} file(s) copied, {count} sessions indexed")
        LAST_OK.touch()
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
    except (ValueError, RecursionError) as exc:
        raise Fail(f"{path} is not valid JSON ({type(exc).__name__}); indexes left as they were")
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
    imp = last_import(archive)
    every = shared_settings(archive)["remind_claude_ai_import_every_days"]
    if imp is None:
        idx += ["**claude.ai sessions:** never saved into this archive. See the README, \"Sessions on claude.ai\".", ""]
    else:
        age = days_since(imp[0])
        when = f"{datetime.fromtimestamp(imp[0]):%Y-%m-%d} ({int(age)} day(s) ago, from {clean(imp[1]) or 'unknown computer'})"
        flag = f" **⚠️ overdue**: the setting is every {every} day(s); export and import again." if age > every else ""
        idx += [f"**claude.ai sessions:** last saved {when}.{flag}", ""]
    n_secrets = secrets_summary(archive)
    if n_secrets and n_secrets[0]:
        flag = f"**⚠️ {n_secrets[1]} need attention**" if n_secrets[1] else "none needs attention"
        idx += [f"**Secrets:** {n_secrets[0]} credential-shaped value(s) are stored in this archive, {flag}; "
                f"see [`SECRETS.md`](SECRETS.md) (values masked).", ""]
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
                               r"\s{0,8}(?P<value>[A-Za-z0-9+/=][A-Za-z0-9+/=\s]{39,16000})?")),
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
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://(?P<user>[^:@/\s\"'\\]{1,100}):(?P<value>[^@/\s\"'\\]{3,200})@"
        r"(?=(?P<host>[A-Za-z0-9._-]{1,100}))")),
    ("JSON Web Token (usually short-lived)", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,2000}\.eyJ[A-Za-z0-9_-]{10,4000}\.[A-Za-z0-9_-]{10,2000}")),
    ("Password or secret assignment", re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?!from_secret)(?P<name>(?:[A-Za-z0-9]{1,20}_){0,4}(?:password|passwd|secret|secret_key|secret_access_key|api_key|"
        r"access_token|auth_token|private_key|client_secret))(?:=|\s{0,3}:\s{1,3})(?P<value>[^\s\"'`\\<>(){}\[\]]{7,199}[^\s\"'`\\<>(){}\[\],;])")),
    ("Password or secret assignment", re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?!from_secret)(?P<name>(?:[A-Za-z0-9]{1,20}_){0,4}(?:password|passwd|pwd|secret|secret_key|api_?key|access_?token|auth_?token|client_secret))\\?[\"']?\s{0,3}[:=]\s{0,3}\\?[\"']"
        r"(?P<value>[^\"'\\\s<>]{8,200})\\?[\"']")),
]
UNQUOTED_ASSIGNMENT = next(rx for k, rx in SECRET_PATTERNS if k == "Password or secret assignment")
# Values that are obviously not real: placeholders, examples, variable references, masks.
PLACEHOLDER = re.compile(r"(?i)(?:x{6}|\*{4}|\.{3}|example|placeholder|changeme|your[_-]|<|\$\{|\$\(|\$[A-Z_]|ENC\[|redacted|dummy|sample|test|correct-horse)")
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
SECRET_SUFFIXES = (".jsonl", ".json", ".md", ".txt", ".sh", ".py", ".js", ".ts", ".yaml", ".yml", ".toml", ".env")


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
    if (archive / "SECRETS.md").exists() or (archive / "data" / "redacted.json").exists():
        # An earlier scan made a salt; it may not have synced to this computer yet. A new one
        # would change every fingerprint and silently break triage, ignore and redaction lists.
        raise Fail(f"{path} is missing although this archive has been scanned before; "
                   "wait for your drive to finish syncing (or restore the file) and try again")
    salt = random_bytes.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, salt + "\n")
    return salt


def finding_fp(kind, value, salt):
    """The fingerprint of a finding: a private key is identified by its body without line breaks,
    so the same key written with real or escaped newlines is one finding."""
    if kind == "Private key":
        value = re.sub(r"\s", "", value)
    return fingerprint(value, salt)


def fingerprint(value, salt=""):
    return hmac.new(salt.encode(), value.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:12]


def looks_random(value):
    return any(c.isdigit() for c in value) and any(c.islower() for c in value) and any(c.isupper() for c in value)


TEST_WORDS = re.compile(r"(?i)\b(?:tests?|testing|fixtures?|mocks?|fakes?|stubs?|stubBundles|examples?|samples?|specs?|seed|dummy)\b")
LOCAL_HOSTS = re.compile(r"(?i)^(?:localhost?|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|\S{0,60}\.(?:local|localhost|test|example|invalid)"
                         r"|(?:\S{0,60}\.)?example\.(?:com|net|org))$")
# Kinds whose format proves nothing about whether they are live: only these may be folded for
# appearing next to test or example wording. A key in a live format needs your verdict.
WORDING_FOLDABLE = ("Password or secret assignment", "Database URL with password")


def find_secrets(line):
    """(kind, value) for each credential in a line; a span already claimed is not matched twice."""
    for kind, value, _start, _end, _context in find_secret_spans(line):
        yield kind, value


def find_secret_spans(line):
    """(kind, value, start, end, context) for each credential in a line. start and end locate the
    value in the line as given; context describes where it was found without containing it."""
    # In transcripts a key often follows a JSON escape such as \\n; the letter would defeat the
    # word-boundary checks below, so escapes become two spaces (same length, so offsets still match).
    line = line.replace("\\n", "  ").replace("\\t", "  ").replace("\\r", "  ")
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
            groups = m.groupdict()
            where_ = []
            if groups.get("user") and groups.get("host"):
                where_.append(f"{groups['user']}@{groups['host']}")
            if groups.get("name"):
                where_.append(groups["name"])
            vstart = m.start("value") if "value" in rx.groupindex else start
            before = line[max(0, vstart - 300):vstart]
            # only the message text: not the session metadata (cwd, branch) earlier on the line
            cut = max(before.rfind('"text":'), before.rfind('"content":'), before.rfind('"stdout":'))
            before = before[cut:] if cut >= 0 else before
            context = {"where": " ".join(where_)[:120], "test": bool(TEST_WORDS.search(before)),
                       "local": bool(groups.get("host") and LOCAL_HOSTS.match(groups["host"]))}
            yield kind, value, vstart, vstart + len(value), context


def secret_units(path):
    """(text, first line number) pieces of a file to scan: each line of a .jsonl transcript (one
    record per line), or the whole of any other file, so a key written over several lines in a
    note is found whole. Read without newline translation, so offsets match the bytes on disk."""
    with open(path, encoding="utf-8", errors="strict", newline="") as fh:
        if path.suffix == ".jsonl":
            for n, line in enumerate(fh, 1):
                yield line, n
        else:
            yield fh.read(), 1


def patterns_version():
    """Changes whenever a pattern does, so cached scan results never outlive the rules that made them."""
    text = ("|".join(f"{k}={rx.pattern}" for k, rx in SECRET_PATTERNS) + PLACEHOLDER.pattern + repr(NEEDS_RANDOM)
            + TEST_WORDS.pattern + LOCAL_HOSTS.pattern + "hit-format-3")
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scan_secrets(archive):
    """{fingerprint: {kind, masked, places}} for every credential under all-sessions/, re-reading only
    files whose size or mtime changed. The cache holds fingerprints, masked forms and line numbers:
    never a value."""
    root = archive / "all-sessions"
    salt = secrets_salt(archive)
    version = patterns_version() + hashlib.sha256(salt.encode()).hexdigest()[:8] + "+setup"
    try:
        cache = json.loads(SECRETS_CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict) or cache.get("v") != version or cache.get("root") != str(root):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("files", {}) if isinstance(cache.get("files"), dict) else {}
    new, files = {}, 0
    setup = archive / "setup"
    candidates = (sorted(root.rglob("*")) if root.is_dir() else []) + \
                 (sorted(setup.rglob("*")) if setup.is_dir() and not setup.is_symlink() else [])
    for path in candidates:
        in_setup = path.is_relative_to(setup) if hasattr(path, "is_relative_to") else str(path).startswith(str(setup) + os.sep)
        if path.is_symlink() or not path.is_file() or (path.suffix not in SECRET_SUFFIXES and not in_setup):
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
            for unit, n in secret_units(path):
                seen = set()  # a line often repeats a value
                for kind, value, start, _e, ctx in find_secret_spans(unit):
                    fp = finding_fp(kind, value, salt)
                    if fp not in seen:
                        seen.add(fp)
                        line_no = n + unit.count("\n", 0, start)
                        hits.append([fp, kind, mask(re.sub(r"\s", "", value) if kind == "Private key" else value, kind),
                                     line_no, ctx["where"], ctx["test"], ctx["local"]])
        except UnicodeDecodeError:  # a binary file (a font, an image): nothing readable to find
            new[rel] = {"sig": sig, "hits": []}
            continue
        except OSError as exc:
            log(f"skip {path}: {type(exc).__name__}")
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
        for fp, kind, masked, n, where_, test, local in new[rel]["hits"]:
            if fp in ignore:
                continue
            h = found.setdefault(fp, {"kind": kind, "masked": masked, "places": [], "where": set(),
                                      "test": 0, "local": 0, "count": 0, "sessions": set()})
            h["count"] += 1
            h["test"] += bool(test)
            h["local"] += bool(local)
            if session_of(rel):
                h["sessions"].add(session_of(rel))
            if where_:
                h["where"].add(where_)
            if len(h["places"]) < 1000:
                h["places"].append(f"{rel}:{n}")
    return files, found


def session_of(rel):
    """The session a file under all-sessions/ belongs to, from the folder layout:
    projects/<project>/<session>.jsonl or projects/<project>/<session>/subagents/...,
    versions/<computer>/<project>/... (same shape), cloud/<session>.json and
    cloud/versions/<session>.<hash>.json. Memory notes belong to no session ("")."""
    parts = rel.split("/")[1:] if rel.startswith("all-sessions/") else rel.split("/")
    if parts[:1] == ["cloud"] and len(parts) >= 2:
        return parts[-1].split(".")[0]
    if parts[:1] == ["projects"]:
        rest = parts[2:]
    elif parts[:1] == ["versions"]:
        rest = parts[3:]
    else:
        return ""
    if len(rest) == 1 and rest[0].endswith(".jsonl"):
        return rest[0][:-len(".jsonl")]
    if len(rest) >= 2 and rest[0] != "memory":
        return rest[0]
    return ""


TRIAGE_STATUSES = {"real": "you marked it real", "dev": "development value", "test": "test value",
                   "example": "example or documentation", "rotated": "rotated", "not-secret": "not a secret"}


def triage_of(h, triage):
    """("attention" | "unlikely", reason) for one finding. Your data/secrets-triage.json wins."""
    t = triage.get(h["fp"]) if isinstance(triage.get(h["fp"]), dict) else None
    if t and t.get("status") in TRIAGE_STATUSES:
        reason = TRIAGE_STATUSES[t["status"]] + (f": {t['note']}" if isinstance(t.get("note"), str) and t["note"] else "")
        return ("attention" if t["status"] == "real" else "unlikely"), reason
    if h["kind"].startswith("JSON Web Token"):
        return "unlikely", "sign-in token, usually expires within hours"
    if h["local"] and h["local"] == h["count"]:
        return "unlikely", "local or example database host"
    if h["test"] and h["test"] == h["count"] and h["kind"] in WORDING_FOLDABLE:
        return "unlikely", "only ever next to test, fixture, seed or example text"
    return "attention", ""


def quote_path(rel):
    """A relative path as a Markdown link target (spaces and other characters percent-encoded)."""
    return "".join(c if re.fullmatch(r"[A-Za-z0-9._~/-]", c) else "".join(f"%{b:02X}" for b in c.encode("utf-8")) for c in rel)


def write_secrets_report(archive, files, found):
    triage = read_data(archive / "data" / "secrets-triage.json", "secrets triage")
    rows = {r["id"]: r for r in scan_all(archive / "all-sessions" / "projects")} if (archive / "all-sessions" / "projects").is_dir() else {}
    rows.update({r["id"]: dict(r, cloud=True) for r in scan_saved(archive / "all-sessions" / "cloud")})
    attention, unlikely = [], []
    for fp, h in found.items():
        h["fp"] = fp
        verdict, reason = triage_of(h, triage)
        (attention if verdict == "attention" else unlikely).append((h, reason))

    def seen_in(h):
        sid = session_of(h["places"][0].rsplit(":", 1)[0])
        r = rows.get(sid, {})
        rel, line = h["places"][0].rsplit(":", 1)
        title = clean(r.get("title") or sid or "file", 60)
        out = f"[{title}]({quote_path(rel)}) (line {line})"
        url = f"https://claude.ai/code/{sid}" if sid.startswith("session_") else (
            f"https://claude.ai/code/session_{r['bridge'][4:]}" if r.get("bridge") else "")
        if url and CLOUD_URL.fullmatch(url):
            out += f" · [claude.ai]({url})"
        return out + (f" · {clean(r.get('start', ''))}" if r.get("start") else "")

    def what(h):  # "@" as an entity, so user@host is not turned into an e-mail link
        return clean(", ".join(sorted(h["where"]))[:100]).replace("@", "&#64;") or ""

    order = lambda pair: (pair[0]["kind"], -len(pair[0]["sessions"]))
    out = [
        "# Secrets found in the archive", "",
        f"Scanned {files} files under `all-sessions/` on {datetime.now():%Y-%m-%d %H:%M}. Do not edit: rebuilt on every sync and by `claude-session-archive secrets`.", "",
        f"**{len(found)} distinct value(s)** found: **{len(attention)} need attention**, {len(unlikely)} are probably not live credentials (folded below).", "",
        "Values are never written here. Passwords show only their length; keys a short public prefix. For a real credential: "
        "**rotate it** with the service that issued it, then optionally replace it in the archive with "
        "`claude-session-archive redact <fingerprint>`. Record your verdict in `data/secrets-triage.json` "
        "(`{\"<fingerprint>\": {\"status\": \"real|dev|test|example|rotated|not-secret\", \"note\": \"...\"}}`); "
        "it moves the row and stays across scans. Links open the transcript (relative to this folder) and the session on claude.ai.", "",
        "## ⚠️ Needs attention", "",
    ]
    if attention:
        out += ["| | Kind | Where it came from | Value | Places | Sessions | First seen in | Fingerprint |", "|---|---|---|---|---|---|---|---|"]
        for h, reason in sorted(attention, key=order):
            out.append(f"| ⚠️ | {clean(h['kind'])} | {what(h)} | `{clean(h['masked'])}` | {h['count']} | {len(h['sessions'])} | {seen_in(h)} | `{h['fp']}` |")
    else:
        out.append("Nothing. Every finding is classified as not a live credential below.")
    out += ["", "<details>", f"<summary>Show the {len(unlikely)} findings that are probably not live credentials</summary>", ""]
    if unlikely:
        out += ["| Kind | Why it is probably not live | Where it came from | Value | Places | Sessions | First seen in | Fingerprint |",
                "|---|---|---|---|---|---|---|---|"]
        for h, reason in sorted(unlikely, key=order):
            out.append(f"| {clean(h['kind'])} | {clean(reason)} | {what(h)} | `{clean(h['masked'])}` | {h['count']} | {len(h['sessions'])} | {seen_in(h)} | `{h['fp']}` |")
    out += ["", "</details>", ""]
    write_atomic(archive / "SECRETS.md", "\n".join(out) + "\n")


def secrets_summary(archive):
    """(total, needing attention) from the last SECRETS.md, or None if there is none."""
    try:
        text = (archive / "SECRETS.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"\*\*(\d{1,7}) distinct value\(s\)\*\* found: \*\*(\d{1,7}) need attention", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


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


# ---------------------------------------------------------------- redact

IN_USE_SECONDS = 600  # a transcript written this recently may belong to a running session
REDACTED = "redacted.json"  # data/redacted.json: fingerprints you redacted, re-applied on every sync and import


DESCRIPTOR_RE = re.compile(r"<secret removed: [^<>\n]{0,300}>")


def distinctive(value):
    """Worth searching for elsewhere: an ordinary word (a password like "postgres") would be found
    all over the transcripts in its ordinary sense, so it is not counted."""
    return len(value) >= 8 and any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def descriptor(kind, where_, fp):
    text = f"<secret removed: {kind}" + (f", {where_}" if where_ else "") + f", {fp}>"
    return re.sub(r'["\\\x00-\x1f]', "", text)  # stays valid inside a JSON string


def redact_text(path, text, targets, salt, values):
    """(new text, {fingerprint: count}) with every target credential replaced at its exact position.
    Raises Fail if a JSON line would stop parsing. Collects the replaced values in `values` (memory
    only) so the caller can look for other copies of the same text."""
    counts = {}
    units = text.split("\n") if path.suffix == ".jsonl" else [text]
    for i, unit in enumerate(units):
        spans = [(st, en, finding_fp(k, v, salt), k, ctx, v) for k, v, st, en, ctx in find_secret_spans(unit)]
        spans = [x for x in spans if x[2] in targets]
        if not spans:
            continue
        new = unit
        for st, en, fp, kind, ctx, value in sorted(spans, reverse=True):
            new = new[:st] + descriptor(kind, ctx["where"], fp) + new[en:]
            counts[fp] = counts.get(fp, 0) + 1
            values.setdefault(fp, set()).add(value.strip())
        if path.suffix in (".jsonl", ".json"):
            pieces = [(unit, new)] if path.suffix == ".jsonl" else list(zip(unit.split("\n"), new.split("\n")))
            for old_l, new_l in pieces:
                try:
                    json.loads(old_l)
                except (ValueError, RecursionError):
                    continue
                try:
                    json.loads(new_l)
                except (ValueError, RecursionError):
                    raise Fail(f"{path}: line {i + 1} would not stay valid JSON; file left unchanged")
        units[i] = new
    return "\n".join(units), counts


def read_exact(path):
    with open(path, encoding="utf-8", errors="strict", newline="") as fh:
        return fh.read()


def rewrite_keeping_times(path, text):
    """Replace a file's content atomically, keeping its permissions and timestamps, so a
    rewritten transcript does not look recently used (or newly grown) to anything else."""
    st = path.stat()
    write_atomic(path, text, mode=st.st_mode & 0o777)
    os.utime(path, (st.st_atime, st.st_mtime))


def load_redacted(archive):
    data = read_data(archive / "data" / REDACTED, "redacted fingerprints").get("redacted", {})
    return {fp: v for fp, v in data.items() if isinstance(fp, str) and re.fullmatch(r"[0-9a-f]{12}", fp)} if isinstance(data, dict) else {}


def enforce_redactions(archive, paths):
    """Re-apply recorded redactions to archive files just written by a sync or an import, so a
    copy from another computer, an unredacted local transcript or a newer claude.ai export
    cannot bring a redacted credential back. Returns the number of values replaced."""
    targets = load_redacted(archive)
    if not targets or not paths:
        return 0
    salt = secrets_salt(archive)
    total = 0
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file() or path.suffix not in SECRET_SUFFIXES:
                continue
            text = read_exact(path)
            new, counts = redact_text(path, text, targets, salt, {})
            if counts:
                rewrite_keeping_times(path, new)
                total += sum(counts.values())
        except (OSError, UnicodeDecodeError, Fail) as exc:
            log(f"could not re-apply redactions to {path}: {exc}")
    if total:
        log(f"re-applied redactions: {total} value(s)")
    return total


def cmd_redact(args):
    """Replace chosen credentials, by fingerprint, with a <descriptor> in the archive and in this
    computer's transcripts, and record them so every later sync and import keeps them out.
    Dry run unless --apply."""
    archive = archive_dir()
    targets = {f.strip().strip("`") for f in args.fingerprints}
    bad = [f for f in targets if not re.fullmatch(r"[0-9a-f]{12}", f)]
    if bad:
        raise Fail(f"not a fingerprint from SECRETS.md: {', '.join(sorted(bad))}")
    salt = secrets_salt(archive)
    arch_root = archive / "all-sessions"
    if not acquire_lock():
        raise Fail("a sync is running; try again in a minute")
    totals, changed, problems, skipped, values, leftover = {}, [], [], [], {}, {}

    def pair_key(root, path):  # the same transcript, locally and in the archive
        try:
            return path.relative_to(PROJECTS if root == PROJECTS else arch_root / "projects").as_posix()
        except ValueError:
            return None

    try:
        plan = []
        for root in (arch_root, PROJECTS, archive / "setup"):
            for path in sorted(root.rglob("*")) if root.is_dir() and not root.is_symlink() else []:
                if path.is_symlink() or not path.is_file() or (path.suffix not in SECRET_SUFFIXES and root != archive / "setup"):
                    continue
                try:
                    text = read_exact(path)
                    new, counts = redact_text(path, text, targets, salt, values)
                except UnicodeDecodeError:
                    problems.append(f"{path}: not UTF-8 text, not checked")
                    continue
                except (OSError, Fail) as exc:
                    problems.append(str(exc))
                    continue
                if counts:
                    plan.append((root, path, new, counts))
        # A transcript in use is skipped, and so is its archive copy: redacting only one of the
        # pair would let the next sync copy the original back beside or over it.
        in_use = set()
        for root, path, _n, _c in plan:
            if root == PROJECTS and time.time() - path.stat().st_mtime < IN_USE_SECONDS:
                in_use.add(path.relative_to(PROJECTS).as_posix())
        for root, path, new, counts in plan:
            if pair_key(root, path) in in_use:
                skipped.append(str(path))
                continue
            if args.apply:
                try:
                    rewrite_keeping_times(path, new)
                except OSError as exc:
                    problems.append(f"{path}: could not write ({type(exc).__name__}); left unchanged")
                    continue
            changed.append(path)
            for fp, n in counts.items():
                totals[fp] = totals.get(fp, 0) + n
        # Other copies of the same text that no pattern recognised (mysql -pVALUE, a quoted form
        # the patterns do not know): counted, never printed.
        if args.apply and values:
            for root in (arch_root, PROJECTS):
                for path in sorted(root.rglob("*")) if root.is_dir() else []:
                    if path.is_symlink() or not path.is_file() or path.suffix not in SECRET_SUFFIXES:
                        continue
                    try:
                        text = read_exact(path)
                    except (OSError, UnicodeDecodeError):
                        continue
                    text = DESCRIPTOR_RE.sub("", text)  # our own descriptors name the user, not the value
                    for fp, vals in values.items():
                        n = sum(text.count(v) for v in vals if distinctive(v))
                        if n:
                            leftover.setdefault(fp, []).append((path, n))
        if args.apply and totals:
            ledger = read_data(archive / "data" / REDACTED, "redacted fingerprints")
            rec = ledger.get("redacted") if isinstance(ledger.get("redacted"), dict) else {}
            for fp in totals:
                rec.setdefault(fp, {"date": f"{datetime.now():%Y-%m-%d}"})
            write_atomic(archive / "data" / REDACTED, json.dumps({"redacted": rec}, indent=2) + "\n")
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
    verb = "replaced" if args.apply else "would replace"
    for fp in sorted(targets):
        n = totals.get(fp, 0)
        print(f"{fp}: {verb} {n} occurrence(s)" + ("" if n or skipped else "  (not found: check the fingerprint against SECRETS.md)"))
    print(f"{len(changed)} file(s) {'changed' if args.apply else 'affected'} (archive and this computer's ~/.claude/projects)")
    for p in skipped:
        print(f"skipped, in use (changed in the last {IN_USE_SECONDS // 60} minutes), with its archive copy: {p}")
    for p in problems:
        print(f"problem: {p}")
    if args.apply:
        for fp, places in sorted(leftover.items()):
            n = sum(c for _p, c in places)
            print(f"{fp}: the same text still appears {n} time(s) in {len(places)} file(s) in a form the scan does not "
                  f"recognise (for example a command-line flag); look for it there. First: {places[0][0]}")
        write_secrets_report(archive, *scan_secrets(archive))
        build_index(archive)
        print("Recorded in data/redacted.json: every later sync and import re-applies these redactions to what it copies. "
              "Other computers' own transcripts still hold the original until you run this there; "
              "claude.ai keeps its own copy of cloud and Remote Control sessions.")
    else:
        print("Dry run: nothing changed. Add --apply to replace them.")
    missing = [fp for fp in targets if not totals.get(fp)]
    return 1 if problems or skipped or missing else 0


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


WRITTEN = []  # files the current import wrote, for enforce_redactions


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
        WRITTEN.append(out)
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
            WRITTEN.append(out)
            return "updated"
    # Different content, or an existing copy that cannot be read: keep both.
    side = dest / "versions" / f"{sid}.{hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]}.json"
    if side.is_symlink() or side.exists():
        return "unchanged"
    side.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(side, body)
    WRITTEN.append(side)
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
        try:
            enforce_redactions(archive, WRITTEN)
        except (Fail, OSError, ValueError) as exc:
            problems.append(f"redactions not re-applied: {exc}")
        if counts:  # something was read from an export: this counts as saving your claude.ai sessions
            write_atomic(archive / "data" / "claude-ai-import.json", json.dumps({
                "last": datetime.now().isoformat(timespec="seconds"), "computer": computer_name(),
                "sessions": sum(counts.values()), "new": counts.get("new", 0)}, indent=2) + "\n")
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
        if any(a.endswith(SCRIPT_NAME) for a in args) and ("sync" in args or "remind" in args):
            return True
        if "claude-session-archive" in cmd and "sync" not in args:
            return True  # shell-form hook from a pre-release version
    return False


def hook_entry(python):
    # Exec form (command + args): no shell, so no quoting or injection issues and the same
    # entry works on macOS, Linux and Windows. async: never delays the start of a session.
    # The reminder runs synchronously (so Claude Code can show what it prints) but only reads two
    # small files, with a short timeout.
    return {"hooks": [{"type": "command", "command": python,
                       "args": [str(INSTALLED), "sync", "--if-stale"], "async": True},
                      {"type": "command", "command": python,
                       "args": [str(INSTALLED), "remind"], "timeout": 10}]}


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
    ensure_shared_settings(archive)
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    me = Path(__file__).resolve()
    if me != INSTALLED.resolve():
        shutil.copy2(me, INSTALLED)
    old = load_config()
    cid = old.get("computer_id") if isinstance(old.get("computer_id"), str) and re.fullmatch(r"[0-9a-f]{8}", old.get("computer_id", "")) \
        else random_bytes.token_hex(4)
    cname = old.get("computer_name") if isinstance(old.get("computer_name"), str) and old.get("computer_name") else computer_name()
    write_atomic(CONF, json.dumps({"archive": str(archive), "python": python, "version": VERSION,
                                   "computer_id": cid, "computer_name": cname}, indent=2) + "\n")
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
    for p in (INSTALLED, CONF, STAMP, LOCK, CACHE, SAVED_CACHE, SECRETS_CACHE, SETTINGS_CACHE, LAST_OK, LAST_REMINDER, WINDOWS_SHIM):
        try:
            p.unlink()
        except OSError:
            pass
    print("removed the installed copy. Left alone: the archive folder, cleanupPeriodDays in settings.json, "
          f"the PATH entry, settings backups and the log ({LOG}).")
    return 0


def reminder_text(archive):
    """The reminder to show, or "" when nothing is due."""
    settings = shared_settings(archive)
    if not settings["reminders"]:
        return ""
    notes = []
    imp = last_import(archive) if archive is not None else None
    every = settings["remind_claude_ai_import_every_days"]
    if archive is not None and (imp is None or days_since(imp[0]) > every):
        age = "have never been saved" if imp is None else f"were last saved {int(days_since(imp[0]))} days ago"
        notes.append(f"Your claude.ai sessions {age} (reminder every {every} days). On claude.ai/code, run "
                     "export-claude-ai.js in the browser console, then: claude-session-archive import <the saved files>.")
    stale = settings["remind_if_sync_older_than_days"]
    if LAST_OK.exists() and days_since(LAST_OK.stat().st_mtime) > stale:
        notes.append(f"This computer's session archive last synced {int(days_since(LAST_OK.stat().st_mtime))} days ago; "
                     "run: claude-session-archive status")
    return " ".join(notes)


def cmd_remind(args):
    """Run by a SessionStart hook: prints a reminder for Claude Code to show, at most once a day.
    Never fails and never slows a session: anything unexpected means no reminder."""
    try:
        if LAST_REMINDER.exists() and days_since(LAST_REMINDER.stat().st_mtime) < 1:
            return 0
        archive = load_config().get("archive")
        archive = Path(archive) if isinstance(archive, str) and archive and Path(archive).is_dir() else None
        text = reminder_text(archive)
        if text:
            print(json.dumps({"systemMessage": "claude-session-archive: " + text}))
            CONF_DIR.mkdir(parents=True, exist_ok=True)
            LAST_REMINDER.touch()
    except Exception as exc:  # a reminder is never worth breaking a session start for
        try:
            log(f"remind skipped: {type(exc).__name__}")
        except Exception:
            pass
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
    archive = Path(conf["archive"]) if isinstance(conf.get("archive"), str) and Path(conf["archive"]).is_dir() else None
    if archive is not None:
        imp = last_import(archive)
        print(f"claude.ai saved {datetime.fromtimestamp(imp[0]):%Y-%m-%d} from {imp[1]}" if imp else "claude.ai saved never")
        print(f"settings        {archive / 'data' / 'settings.json'}")
        for k, v in shared_settings(archive).items():
            print(f"  {k} = {json.dumps(v)}")
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
    rd = sub.add_parser("redact", help="replace chosen credentials with a <descriptor> (dry run unless --apply)")
    rd.add_argument("fingerprints", nargs="+", help="fingerprints from SECRETS.md")
    rd.add_argument("--apply", action="store_true", help="really change the files")
    rd.set_defaults(fn=cmd_redact)
    sc = sub.add_parser("secrets", help="scan the archive for credentials, report them masked")
    sc.add_argument("--fail", action="store_true", help="exit 1 if anything is found")
    sc.set_defaults(fn=cmd_secrets)
    sub.add_parser("status", help="show configuration").set_defaults(fn=cmd_status)
    sub.add_parser("remind", help="(run by the hook) print a reminder when something is due").set_defaults(fn=cmd_remind)
    mm = sub.add_parser("memory", help="share memory notes between your computers: status, link, forget")
    mm.add_argument("action", nargs="?", default="status", choices=["status", "link", "unlink", "forget"])
    mm.add_argument("args", nargs="*")
    mm.add_argument("--apply", action="store_true", help="link: really link (otherwise a dry run)")
    mm.add_argument("--force", action="store_true", help="link: allow linking two projects that are both on this computer")
    mm.set_defaults(fn=cmd_memory)
    su = sub.add_parser("setup", help="setup restore: bring your Claude Code setup onto this computer")
    su.add_argument("action", choices=["restore"])
    su.add_argument("--from", dest="source", help="the computer to restore from (as listed)")
    su.add_argument("--apply", action="store_true", help="really change ~/.claude")
    su.add_argument("--with-hooks", action="store_true", help="also add hooks and the status line (each is shown first)")
    su.set_defaults(fn=cmd_setup)
    sub.add_parser("uninstall", help="remove the hook and installed copy").set_defaults(fn=cmd_uninstall)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Fail as exc:
        log(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
