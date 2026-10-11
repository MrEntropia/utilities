# Changelog

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
