# Changelog

## 1.6.0 (2026-10-12)

- Memory on every computer: each sync mirrors this computer's memory notes to `all-sessions/memory/<computer>/` and brings here, for projects this computer has, the notes other computers have and this one does not. `memory` lists projects and differences; `memory link <project> <folder> [--apply]` and `memory unlink` join and separate a project that lives elsewhere; `memory forget` drops a note everywhere. Nothing local is overwritten or written through; `MEMORY.md` is merged line by line; a note deleted on one computer is not handed back (recorded by content, per computer); a symlinked or unreadable memory folder is left alone; the home folder is never shared without a link.
- Setup backup: each sync saves this computer's Claude Code setup (skills, agents, commands, hooks, output styles, `CLAUDE.md`, settings, plugin lists) to `setup/<computer>/`, the newest 10 previous versions per file under `history/`. Each computer gets a stable name and random id at install. Every setup file is scanned for secrets; `redact` covers `setup/`; quoted JSON keys like `"DB_PASSWORD": "..."` are found.
- `setup restore [--from COMPUTER] [--apply] [--with-hooks]`: explains itself, then (dry run by default) copies setup files you do not have and merges `settings.json`, showing every permission rule; hooks and the status line only with `--with-hooks`, each command printed in full; API helpers, permission mode, safeguard switches and environment variables that redirect requests are never restored automatically, only listed.

The first version of these features was reviewed independently before release (3 High, 6 Medium, 10 Low, 4 Info); everything here is the fixed version (see `securityaudit.md`).

## 1.5.0 (2026-10-12)

- Shared settings in the archive, `data/settings.json`, created on install and read by every computer: `sync_every_hours`, `scan_secrets_on_sync`, `reminders`, `remind_claude_ai_import_every_days`, `remind_if_sync_older_than_days`. Values are type- and range-checked; an unreadable file means the last good settings.
- Reminders: a second SessionStart hook (`remind`, synchronous, at most once a day, never fails) shows a one-line reminder in Claude Code when your claude.ai sessions are due to be saved again or this computer's sync has stopped. `import` records each check-in in `data/claude-ai-import.json`; `INDEX.md` shows when they were last saved and flags it when overdue. `status` shows the settings in effect and the last import.

## 1.4.0 (2026-10-12)

Fixes from the third independent review (R1 to R10, see `securityaudit.md`), all on `redact` and the `SECRETS.md` triage:

- A private key is found and redacted whole, header to footer, also when a transcript stores it with escaped line breaks (before, only its first line was replaced and the report then went quiet).
- Redactions stick: `data/redacted.json` records the fingerprints you redacted, and every sync and import re-applies them to what it copies.
- A transcript skipped as in use is skipped together with its archive copy; rewritten files keep their timestamps and line endings.
- Values containing `, ; ( ) [ ] { }` are replaced whole; other copies of the same text in forms the scan does not know are reported (never printed).
- Non-UTF-8 files, unknown fingerprints and write failures are reported per file with exit code 1, never a traceback.
- Triage: test words count only as whole words in the message text; `.internal` and bare names like `db` no longer count as local hosts; a live-format key is never folded on wording; `@` in the report cannot become an e-mail link; correct session counts for `versions/` copies and memory notes.
- A missing `data/.secrets-salt` in an archive that has been scanned before stops the scan with a message instead of silently creating a new one.

## 1.3.0 (2026-10-12)

- `SECRETS.md` is sorted into **Needs attention** (marked ⚠️) and a folded section (a `<details>` toggle) for findings that are probably not live: short-lived sign-in tokens, local or example database hosts, values only ever next to test, fixture, seed or example text. Each row says where the value came from (database user and host, or variable name, never the value), how many places and sessions hold it, and links to the first transcript (relative path) and to the session on claude.ai. `INDEX.md` shows how many need attention.
- `data/secrets-triage.json` records your verdict per fingerprint (real, dev, test, example, rotated, not-secret, with a note) and overrides the automatic sorting.
- `redact FINGERPRINT... [--apply]` replaces chosen credentials with a `<secret removed: ...>` descriptor, in the archive and in this computer's transcripts, at their exact position. Dry run by default; skips transcripts written in the last 10 minutes; never writes a line that would stop being valid JSON.
- `$(VAR)` references (Kubernetes) are treated as placeholders, not secrets.

## 1.2.0 (2026-10-11)

- Fixes for every finding of the second security review (F1 to F12, see `securityaudit.md`): no links, images, HTML or auto-linked URLs from session text in the indexes; a malformed saved record no longer stops syncing; `import` never replaces a copy that has events the new one lacks (different copies are kept beside it), survives bad files, caps record size, refuses symlinked folders, takes the sync lock and lists what it adds; the scan finds keys after JSON escapes and `DB_PASSWORD=`-style assignments, fingerprints each private key by its body, keys fingerprints with a salt, shows only the length of passwords and stays fast on lines with thousands of matches; `uninstall` removes every cache.
- Every sync runs the secrets scan, incrementally: only new and grown files are read, from a cache that holds fingerprints, masked forms and line numbers, never values, and that resets when the patterns change. `INDEX.md` opens with the count and links `SECRETS.md`. A failed scan never stops a sync.

## 1.1.0 (2026-10-11)

- `import FILE...`: files sessions exported from claude.ai (gzip or plain JSON Lines, or the older single-object JSON) into `all-sessions/cloud/session_<id>.json`. Session ids are validated before they become file names; an existing copy is only replaced by one holding more events.
- `export-claude-ai.js`: a short script you run in your own browser on claude.ai to download every cloud and Remote Control session claude.ai still has.
- The index builds rows from saved copies: cloud sessions (no `cloud.json` entry needed) and **recovered** Remote Control sessions whose local transcript is gone. A local session whose claude.ai copy was also saved is not listed twice.
- `digest` covers saved claude.ai sessions.
- `secrets [--fail]`: scans the archive for credentials with a recognisable shape and writes `SECRETS.md`, with every value masked; `data/secrets-ignore.json` silences fingerprints.

## 1.0.2 (2026-10-11)

- A cloud session transcript saved as `all-sessions/cloud/session_<id>.json` is linked from its `SESSIONS.md` entry instead of "not archived".

## 1.0.1 (2026-10-11)

- Cloud sessions in `data/cloud.json` take `started_from`, `branch`, `last` and `first_request`; the Where column shows `Cloud · <started_from> · <repo> (<branch>)` instead of only `Cloud (claude.ai)`, and `SESSIONS.md` shows the date range and first request.

## 1.0.0 (2026-10-11)

- `install`: sets `cleanupPeriodDays` to 36500 so Claude Code stops deleting transcripts after 30 days, adds an exec-form async `SessionStart` hook (no shell), installs a copy of the script and a `claude-session-archive` command, backs up `settings.json` first; re-running it replaces its own hook instead of adding another.
- `sync [--if-stale]`: additive copy of `~/.claude/projects` (transcripts and project memory) into the archive folder, at most once per 20 hours per computer from the hook; never deletes, never replaces a transcript with a shorter one (other differing versions are kept under `versions/<computer>/`).
- Indexes built from the archive, so they cover every computer: `SESSIONS.md` (full entries) and `INDEX.md` (one line per session by group, with type, where it ran, and a count by type). Both fully generated; your own notes live in `data/curated.md` and are shown at the end of `INDEX.md`.
- Where each session ran: the computer (each computer records its own sessions in `all-sessions/computers/<computer>.json`), the app (Terminal, Desktop app, VS Code, SDK, from the transcript), and, for a session that had Remote Control on, a link to it on claude.ai.
- Cloud sessions (claude.ai/code), which have no transcript on any computer, can be listed in `data/cloud.json` so they appear in the index with their links.
- Groups from a `Group: ` title prefix or `data/groups.json`; summaries, keep notes and types from `data/summaries.json`.
- `digest`: condensed per-session text (user messages and assistant prose only) for summarising, `--missing-only`.
- `status`, `index`, `uninstall`.
- `install.sh` (macOS, Linux) and `install.ps1` (Windows) find Python 3.8+ or install it (Homebrew or Command Line Tools, the Linux package manager, or `winget` per-user), and put the command on `PATH`.
