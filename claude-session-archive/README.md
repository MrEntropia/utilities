# claude-session-archive

Keep your [Claude Code](https://code.claude.com) session history: stop it being deleted, back it up to a folder you choose (typically one your cloud drive syncs), and get a readable index of every session, grouped, typed and summarised.

## Why

Claude Code saves each session as a transcript in `~/.claude/projects/` and, by default, **deletes transcripts older than 30 days** every time it starts (`cleanupPeriodDays`). Ideas, decisions and investigations from a month ago are gone, and `claude --resume` cannot bring them back. A desktop app sidebar group is only display state; losing it loses your way back to the sessions too.

This tool:

1. sets `cleanupPeriodDays` to 36500 (100 years), so nothing is deleted from now on;
2. copies new and grown transcripts to an archive folder once a day, from every computer you install it on, and never deletes or shrinks anything there;
3. writes two indexes into that folder: `INDEX.md` (one line per session, by group, with its type and where it ran) and `SESSIONS.md` (every session in full: dates, project, where it ran, summary, a "Keep" note, first request, transcript path).

**Transcripts of local sessions only.** It archives sessions that ran on your computers (the terminal, the desktop app, IDE extensions). Sessions that ran in the cloud on [claude.ai/code](https://claude.ai/code) are stored by Anthropic, not on your disk, so their transcripts cannot be archived; you can still list them in the index, see [Cloud sessions](#cloud-sessions).

## What you get

```
Claude Session Archive/
  INDEX.md            one line per session by group, then your curated notes (generated: do not edit)
  SESSIONS.md         every session in full (generated: do not edit)
  LAST-SYNC.txt       when, and from which computer, the last sync ran
  data/               yours: the sync reads these and never writes them (except creating curated.md once)
    groups.json       which group each session belongs to
    summaries.json    summary, keep note and type per session
    cloud.json        cloud sessions (claude.ai/code) to list in the index
    curated.md        your notes on projects worth keeping; shown at the end of INDEX.md
  all-sessions/
    projects/...      copies of every transcript and Claude Code project memory
    computers/<computer>.json   which sessions each computer holds (each computer writes only its own)
    versions/<computer>/...   any copy that differs from the archived one without simply having grown
                              (e.g. a memory note edited on two computers): kept beside it, never over it
```

## Requirements

| | |
|---|---|
| Claude Code | a version with exec-form hooks (`command` + `args`); tested with 2.1.296 |
| Python | 3.8 or newer, standard library only. **The installers install it for you if it is missing.** |
| A folder to archive into | any folder works; put it inside Google Drive, OneDrive, iCloud Drive or Dropbox to get an off-machine backup |
| OS | macOS, Windows 10/11, Linux |

No other packages, no accounts, no API keys.

## Install

Get the code first (or download the repository as a ZIP and unpack it):

```sh
git clone https://github.com/MrEntropia/utilities.git
cd utilities/claude-session-archive
```

### macOS and Linux

```sh
sh install.sh --archive "$HOME/Library/CloudStorage/GoogleDrive-you@example.com/My Drive/Claude Session Archive"
```

- Leave out `--archive` and the installer uses `Claude Session Archive` inside your synced drive if it finds exactly one (Google Drive, OneDrive or iCloud on macOS); if it finds none or several it lists them and asks you to choose.
- **No Python 3.8+?** On macOS it uses Homebrew if you have it, otherwise it opens Apple's Command Line Tools installer (which includes Python); finish that and run `install.sh` again. On Linux it uses `apt`, `dnf`, `pacman` or `zypper` (asks for your password).
- It adds `~/.local/bin` to your `PATH` in your shell's rc file if it is not there, so `claude-session-archive` works in new terminals.

### Windows

In PowerShell, from the `claude-session-archive` folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Archive "G:\My Drive\Claude Session Archive"
```

- Leave out `-Archive` to use the synced drive it finds (Google Drive's `My Drive` on any drive letter, or OneDrive).
- **No Python 3.8+?** It installs Python 3.12 for your user only with `winget` (no administrator rights needed) and picks it up in the same window. It ignores the Microsoft Store `python.exe` placeholder. Without `winget`, it points you to python.org.
- It adds `%APPDATA%\claude-session-archive` to your user `PATH` for the `claude-session-archive` command.

### On a second computer

Run the same installer with the same archive folder (once your drive has synced it). Each computer adds its own sessions; the indexes always cover every computer's sessions, because they are built from the archive, not from the local machine.

## Daily use

Nothing to do. When you start a Claude Code session, the hook runs a sync in the background, at most once every 20 hours per computer; it never slows a session down.

```sh
claude-session-archive status          # archive, hook, retention, last sync, log location
claude-session-archive sync            # sync now
claude-session-archive index           # only rebuild INDEX.md and SESSIONS.md (after editing data/)
claude-session-archive digest OUTDIR --missing-only   # see "Summaries" below
```

### Groups

Two ways, and the first wins:

- **Title prefix.** Rename a session so its title starts with the group: `/rename Marketing: launch post ideas`. It is stored in the transcript, so it survives anything, and `claude --resume` can search for `Marketing:`.
- **`data/groups.json`**, mapping group names to session-id prefixes (the first 8 characters are enough):

```json
{ "groups": {
    "Marketing": ["a1b2c3d4", "e5f6a7b8"],
    "Customer app": ["0f1e2d3c"]
} }
```

Sessions in neither land under "Ungrouped". Groups appear in the order of the file.

### Where each session ran

The **Where** column says which computer the session is on and which app ran it, for example `my-laptop · Terminal, Remote Control` or `Cloud (claude.ai)`:

- **Computer**: every sync writes `all-sessions/computers/<computer>.json`, listing the sessions on that computer. A session copied before its computer ran this version shows `computer not recorded` until that computer syncs again.
- **App**: read from the transcript (`Terminal`, `Desktop app`, `VS Code`, `SDK / headless`; anything else is shown as Claude Code wrote it).
- **Remote Control**: a local session that was reachable from claude.ai or the mobile app records its claude.ai address. Its title in `INDEX.md` links there, and `SESSIONS.md` prints the address. The link is only useful while claude.ai still has the session; the transcript in the archive is the copy that lasts.

### Summaries and types

`data/summaries.json` holds, per full session id:

```json
{ "a1b2c3d4-...": {
    "summary": "Two to four sentences: what was asked, what was done, how it ended.",
    "keep": "One sentence worth remembering: a decision, a root cause, an open idea.",
    "kind": "Bug fix"
} }
```

`kind` is free text; useful values are `Bug fix`, `Feature`, `Investigation`, `Research`, `Setup`. `INDEX.md` shows it as a column and counts sessions by type.

The sync does not write summaries (it never calls a model). To add them, make digests, which hold only your messages and the assistant's prose (no tool output, roughly a thousand times smaller than the transcripts):

```sh
claude-session-archive digest ~/session-digests --missing-only
```

then ask Claude Code something like: *"Read every file in ~/session-digests and add an entry for each to `<archive>/data/summaries.json` with summary, keep and kind, as described in the claude-session-archive README."* Then run `claude-session-archive index`.

## Privacy and security

What the tool does and does not do, so you can verify it (it is one file, `claude_session_archive.py`):

- **No network access.** The script makes no network requests and runs no other programs. Anything that reaches the cloud gets there only because *you* put the archive inside a folder that your own sync client uploads. The installers use the network only to install Python if it is missing (Homebrew, your Linux package manager, or `winget`).
- **Reads** `~/.claude/projects/` and `~/.claude/settings.json`. **Writes** the archive folder, its own config folder (`~/.config/claude-session-archive/`, or `%APPDATA%\claude-session-archive`), the command shim, `~/.claude/settings.json` (after saving a timestamped backup next to it, and only if it is valid JSON) and, if needed, one PATH line in your shell rc file / user PATH.
- **Never deletes** anything in the archive, and never touches your transcripts.
- **The hook runs no shell.** It is registered in exec form (an absolute path to Python plus arguments), so paths with spaces or quotes cannot be misread and nothing is interpreted by a shell.

**Your transcripts are sensitive, and the archive is a copy of them.** A transcript contains everything said in the session and much of what was read or run in it: source code, file contents, command output, and anything you pasted, including tokens, passwords, API keys or customer data. `SESSIONS.md` and `INDEX.md` quote each session's first request. So:

- Keep the archive in a **private** folder. Do not put it in a shared drive or a folder shared with others, and do not commit it to a repository.
- If a secret was pasted into a session, treat it as stored wherever the archive is synced: rotate it, then delete it from the transcript copy if you want it gone.
- Your cloud provider can read what you store there unless you use end-to-end encryption (for example iCloud Advanced Data Protection, or an encrypted folder such as Cryptomator).
- Anyone who can write to the archive's `data/` files can change what the indexes say, but not run code: the tool only reads JSON and Markdown from there, and escapes it before writing the indexes. Links are only written for `https://claude.ai/` addresses.

What else the archive reveals, beyond the conversations themselves:

- `LAST-SYNC.txt`, the `Where` column and `all-sessions/computers/` name your computers (computer names often include your name).
- Session links identify sessions in your claude.ai account. Treat them like the rest of the archive: private.
- `SESSIONS.md` and the folder names under `all-sessions/projects/` contain the full paths of the folders you worked in, which can include your username and client or project names.
- The copies include subagent transcripts and `tool-results` files: full contents of files that were read and full output of commands that were run.
- **Keeping transcripts forever is itself a trade-off.** With `cleanupPeriodDays` at 36500, a secret pasted into a session also stays in `~/.claude` on your computer indefinitely, not only in the archive.
- Digests (`digest`) contain your messages (up to 1500 characters each), including anything you pasted. Handing them to a new Claude session to summarise sends that text to the model provider again and records it in the new session's transcript, which is archived too.
- The settings backups the installer writes (`~/.claude/settings.json.bak-<timestamp>`, the newest 3 kept, readable by you only) contain whatever your settings contain, including any `env` tokens.

The archive, indexes, config and log are created readable by you only (permissions 0600 / 0700 on macOS and Linux). An independent review of the code, its findings, and the tests that pin each fix are described in [securityaudit.md](securityaudit.md); run them with `python3 -m unittest discover -s tests`.

## Cloud sessions

Sessions that ran on claude.ai/code ("Claude Code on the web") live on Anthropic's servers. Nothing on your computer holds them, so this tool cannot archive them, and according to [Anthropic's data usage page](https://code.claude.com/docs/en/data-usage) they follow your account's retention policy (30 days for commercial accounts; 30 days or 5 years for consumer accounts, depending on your training setting). The documented way to bring one to your computer is `claude --teleport <session-id>`, run inside a clean checkout of the same repository; it checks out the session's branch and loads its full conversation into your terminal. The docs do not say whether that writes a local transcript this tool would then pick up, and there is no documented bulk export.

So that cloud sessions are not forgotten, list them in `data/cloud.json`; they then appear in `INDEX.md` and `SESSIONS.md` under their group, marked `Cloud (claude.ai)`, with a link:

```json
{ "sessions": [
    { "title": "Pricing page ideas", "url": "https://claude.ai/code/session_01ExampleOnly",
      "date": "2026-09-30", "group": "Marketing", "repo": "you/your-repo",
      "summary": "What was asked, what was done, how it ended.", "keep": "One thing worth remembering.", "kind": "Research" }
] }
```

Only `title` is required. Copy `url` from the browser's address bar while the session is open; only `https://claude.ai/` links are accepted. The summary is the only record of the content that lasts, so write it (or ask the cloud session to write it) before the session expires.

## Uninstall

```sh
sh install.sh --uninstall                                            # macOS / Linux
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall    # Windows
```

Removes the hook, the installed copy and the command (only if the command is this tool's). Left alone on purpose: the archive, the `cleanupPeriodDays` setting (set it back yourself if you want deletion again), the PATH line, the settings backups and the log.

## Troubleshooting

- **`status` says the last sync was long ago.** The sync only runs when a Claude Code session starts. Run `claude-session-archive sync` and read the log path that `status` prints.
- **"archive not reachable".** The cloud drive was not mounted or not signed in; the next session start tries again.
- **Why not a nightly scheduled job?** macOS refuses background agents (launchd) write access to cloud-drive folders under `~/Library/CloudStorage` ("Operation not permitted") unless you give them Full Disk Access. Running from Claude Code's own hook inherits your terminal's access and needs no extra permission, and transcripts only change while Claude Code is in use anyway.
- **Moved or updated the repository?** The hook points at the installed copy, not at your checkout, so moving the checkout breaks nothing. After `git pull`, run the installer again to update the installed copy.
