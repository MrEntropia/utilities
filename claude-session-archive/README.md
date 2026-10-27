# claude-session-archive

Keep your [Claude Code](https://code.claude.com) session history: stop it being deleted, back it up to a folder you choose (typically one your cloud drive syncs), and get a readable index of every session, grouped, typed and summarised.

## Why

Claude Code saves each session as a transcript in `~/.claude/projects/` and, by default, **deletes transcripts older than 30 days** every time it starts (`cleanupPeriodDays`). Ideas, decisions and investigations from a month ago are gone, and `claude --resume` cannot bring them back. A desktop app sidebar group is only display state; losing it loses your way back to the sessions too.

This tool:

1. sets `cleanupPeriodDays` to 36500 (100 years), so nothing is deleted from now on;
2. copies new and grown transcripts to an archive folder once a day, from every computer you install it on, and never deletes or shrinks anything there;
3. writes two indexes into that folder: `INDEX.md` (one line per session, by group, with its type and where it ran) and `SESSIONS.md` (every session in full: dates, project, where it ran, summary, a "Keep" note, first request, transcript path).

## Features

| Feature | What it does | Command | Since |
|---|---|---|---|
| Keep everything | Stops Claude Code deleting transcripts after 30 days; copies them to your archive daily from every computer, never deleting or shrinking a copy | `install`, then automatic | 1.0 |
| Index | `INDEX.md` and `SESSIONS.md`: every session by group, with type, summary, a "Keep" note, its first request and its transcript | automatic, `index` | 1.0 |
| Groups, summaries, types | Your own grouping (`data/groups.json` or a `Group:` title prefix) and summaries (`data/summaries.json`), with condensed digests to write them from | `digest` | 1.0 |
| Where it ran | The computer, the app (Terminal, Desktop app, VS Code, SDK), Remote Control, and a link to the session on claude.ai | automatic | 1.0 |
| Sessions on claude.ai | Brings in cloud sessions and Remote Control sessions whose local transcript was already deleted, from an export you make in your browser | `export-claude-ai.js`, `import` | 1.1 |
| Secrets scan | Finds passwords, API keys, private keys, tokens and database URLs in everything the archive holds, on every sync, and lists them in `SECRETS.md` with values masked | automatic, `secrets` | 1.1, in every sync since 1.2 |
| Secrets triage | `SECRETS.md` puts what needs attention first and folds away what is probably not live (with the reason); your verdicts in `data/secrets-triage.json` override it | automatic | 1.3 |
| Shared settings | One `data/settings.json` in the archive that every computer reads: sync interval, secrets scan on or off, reminders | edit the file | 1.5 |
| Reminders | When a Claude Code session starts and your claude.ai sessions are due to be saved again, or this computer's sync has stopped, a one-line reminder with the steps; `INDEX.md` shows when they were last saved | automatic | 1.5 |
| Shared memory | Claude Code's memory notes follow your projects to every computer: notes another computer has are brought here on each sync; nothing here is overwritten, and a note you delete is not handed back | automatic, `memory link`, `memory unlink`, `memory forget` | 1.6 |
| Setup backup and restore | Each sync saves this computer's Claude Code setup (skills, agents, commands, hooks, output styles, `CLAUDE.md`, settings); `setup restore` brings it onto a new computer, explaining first what it will and will not do | automatic, `setup restore` | 1.6 |
| Redact | Replaces a credential you choose with a `<secret removed: ...>` descriptor in the archive and in this computer's transcripts, and keeps it out on every later sync and import; dry run unless `--apply` | `redact` | 1.3, safeguards 1.4 |

Every feature has been through an independent security review; what each review found, how it was fixed and how it was tested is in [securityaudit.md](securityaudit.md).

## What you get

```
Claude Session Archive/
  INDEX.md            one line per session by group, then your curated notes (generated: do not edit)
  SESSIONS.md         every session in full (generated: do not edit)
  SECRETS.md          credentials found by `secrets`, masked (generated: do not edit)
  LAST-SYNC.txt       when, and from which computer, the last sync ran
  data/               yours: the sync reads these and never writes them (except creating curated.md once)
    groups.json       which group each session belongs to
    summaries.json    summary, keep note and type per session
    cloud.json        optional details for cloud sessions (claude.ai/code)
    secrets-ignore.json   fingerprints that `secrets` should stop reporting
    secrets-triage.json   your verdict on findings (real, dev, test, example, rotated, not-secret)
    settings.json     shared settings, read by every computer (created on install)
    claude-ai-import.json  when your claude.ai sessions were last imported, and from which computer
    memory-projects.json   project folders that are the same project on different computers (`memory link`)
    memory-forgotten/<computer>.json  memory notes deleted on purpose (by content), never handed back
    curated.md        your notes on projects worth keeping; shown at the end of INDEX.md
  setup/<computer>/   that computer's Claude Code setup (files/, history/<date>/, about.json)
  all-sessions/
    projects/...      copies of every transcript and Claude Code project memory
    computers/<computer>.json   which sessions each computer holds (each computer writes only its own)
    cloud/session_<id>.json     sessions saved from claude.ai with `import`
    memory/<computer>/<project folder>/   each computer's memory notes as they are now (written only by that computer)
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
claude-session-archive import FILE...  # file sessions exported from claude.ai, see "Sessions on claude.ai"
claude-session-archive secrets         # scan for credentials, see "Scanning for secrets"
claude-session-archive redact FP...    # replace chosen credentials with a <descriptor> (dry run unless --apply)
```

### Memory on every computer

Claude Code keeps memory notes per project, in `~/.claude/projects/<project folder>/memory/`. Each sync mirrors this computer's notes, as they are now, to `all-sessions/memory/<computer>/` in the archive (only that computer writes there), and brings here, for every project this computer has, the notes your other computers have and this one does not.

- **Same project, same place** (same path on both computers): shared automatically. Sessions started in your home folder itself are never shared without a link.
- **Same project, different place** (another user name, another folder): link it once, on any computer:

  ```sh
  claude-session-archive memory                                              # projects with notes, and what would come here
  claude-session-archive memory link Rocket-scanner ~/code/Rocket-scanner    # dry run: says what it would bring
  claude-session-archive memory link Rocket-scanner ~/code/Rocket-scanner --apply
  claude-session-archive memory unlink ~/code/Rocket-scanner                 # stop sharing (notes already here stay)
  ```

  `link` refuses to join two projects that are both on this computer unless you add `--force`.
- **Nothing here is ever overwritten.** A note that exists on this computer stays as it is, even if another computer's copy is newer; `memory` lists notes that differ so you can reconcile them by hand. A note is never written through a symlink, and on macOS and Windows a note that differs only in upper and lower case counts as the same note. `MEMORY.md`, the index, is merged: lines pointing to notes this computer now has are added after your own (300 characters at most each), nothing is removed.
- **A note you delete stays deleted.** Each computer remembers which notes it has had; one that disappears was deleted on purpose: it is not brought back, and its content is recorded in `data/memory-forgotten/<computer>.json` so no other computer hands that version back (a later note with the same name but new content still travels). Other computers keep their own copies. More than a few notes vanishing from one project in one go looks like an accident and is only reported; use `memory forget <project> <note>` to drop a note everywhere on purpose. A memory folder that is a symlink or cannot be read is left alone, never read as "everything deleted".
- **First run.** On a computer that already had memory before sharing started, notes it deleted earlier are not brought back.
- **Not supported:** a custom memory location (`autoMemoryDirectory` in settings, or `CLAUDE_CODE_REMOTE_MEMORY_DIR`); sharing is then off.

Anyone who can write to the archive folder can put text into every computer's memory notes, which Claude reads. Keep the archive private.

### Your Claude Code setup on a new computer

Each sync saves this computer's Claude Code setup to `setup/<computer>/` in the archive (the computer is named once at install, with a random id, so a changing network name does not split it): `CLAUDE.md`, `settings.json`, `keybindings.json`, and the `skills`, `agents`, `commands`, `hooks` and `output-styles` folders (and the parked `skills-disabled` and `agents-disabled`), plus the lists of installed plugins and marketplaces. Caches, history, transcripts and credentials are not part of it; `node_modules`, `.git` and files over 5 MB are skipped, and so are symlinked items. A file that changed is replaced and its previous version kept under `setup/<computer>/history/<date>/` (the newest 10 per file). Every file is scanned for secrets, and `redact` covers this folder too. Files you delete are not removed from the backup.

On a new computer, after `install`:

```sh
claude-session-archive setup restore                       # explains, then shows what it would do
claude-session-archive setup restore --apply               # copies files, merges settings
claude-session-archive setup restore --apply --with-hooks  # also adds hooks and the status line
claude-session-archive setup restore --from <computer> --apply   # if several computers saved a setup
```

It begins by explaining itself. In short:

- **Files you do not have** are copied into `~/.claude`, and only files that belong to a setup (nothing else in that folder, such as credentials, is ever copied). A file you already have is never overwritten; differing files are listed.
- **`settings.json` is merged, not copied:** settings this computer lacks are added and every added permission rule is shown; paths in the other computer's home folder are rewritten to this one's; settings you already have are kept; environment variables are listed by name. Your `settings.json` is backed up first. This tool's own hooks are left to `install`.
- **What runs programs is opt-in:** hooks, the status line and file-suggestion commands are added only with `--with-hooks`, and every command is printed in full in the dry run, under "These run programs on this computer".
- **What changes where your data goes, or switches safeguards off, is never restored automatically:** API key and credential helpers, `permissions.defaultMode`, `disableAllHooks`, MCP auto-approval, `autoMemoryDirectory`, and environment variables such as `ANTHROPIC_BASE_URL`, `*_PROXY`, `NODE_OPTIONS` or `PATH`. They are listed with their values (except secrets) for you to copy by hand if you trust them.
- **It does not** sign you in (run `claude` and log in), install plugins (it lists those the other computer had; add them with `/plugin`), or delete anything. Memory notes follow your projects instead.

Only restore from your own archive: whoever can write to that folder decides what this copies.

### Settings

`data/settings.json` in the archive is created on install with the defaults and read by every computer that syncs the archive, so you change a setting once:

```json
{
  "sync_every_hours": 20,
  "scan_secrets_on_sync": true,
  "reminders": true,
  "remind_claude_ai_import_every_days": 14,
  "remind_if_sync_older_than_days": 3
}
```

| Setting | Meaning | Allowed |
|---|---|---|
| `sync_every_hours` | at most one sync per this many hours, started by a Claude Code session | 1 to 720 |
| `scan_secrets_on_sync` | run the secrets scan as part of each sync | true / false |
| `reminders` | show reminders when a Claude Code session starts | true / false |
| `remind_claude_ai_import_every_days` | remind you to save your claude.ai sessions this often | 1 to 365 |
| `remind_if_sync_older_than_days` | remind you if this computer has not synced for this long | 1 to 365 |

A value that is missing, of the wrong type or out of range falls back to its default; an unreadable file (for example while your drive is still syncing it) means the last good settings this computer saw. `claude-session-archive status` prints the settings in effect.

### Reminders

Your claude.ai sessions only reach the archive when you export and import them, so the tool reminds you:

- **In Claude Code.** A second, very small hook runs when a session starts. If your last import is older than `remind_claude_ai_import_every_days`, or never happened, or this computer's last successful sync is older than `remind_if_sync_older_than_days`, Claude Code shows one line with the steps, for example *"claude-session-archive: Your claude.ai sessions were last saved 16 days ago (reminder every 14 days). On claude.ai/code, run export-claude-ai.js in the browser console, then: claude-session-archive import <the saved files>."* At most once a day. It only reads two small files, never fails a session start, and is silent when nothing is due.
- **In `INDEX.md`.** It opens with *"claude.ai sessions: last saved 2026-10-12 (3 day(s) ago, from my-laptop)"*, with **⚠️ overdue** added when the reminder interval has passed.

An import counts as saving your claude.ai sessions whenever it reads at least one session from the files you give it.

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
- **`export-claude-ai.js` is separate and runs only when you paste it into your browser.** It sends GET requests to claude.ai, with your own sign-in, and downloads files to your computer; it sends nothing anywhere else.
- **The hook runs no shell.** It is registered in exec form (an absolute path to Python plus arguments), so paths with spaces or quotes cannot be misread and nothing is interpreted by a shell.

**Your transcripts are sensitive, and the archive is a copy of them.** A transcript contains everything said in the session and much of what was read or run in it: source code, file contents, command output, and anything you pasted, including tokens, passwords, API keys or customer data. `SESSIONS.md` and `INDEX.md` quote each session's first request. So:

- Keep the archive in a **private** folder. Do not put it in a shared drive or a folder shared with others, and do not commit it to a repository.
- If a secret was pasted into a session, treat it as stored wherever the archive is synced: rotate it, then delete it from the transcript copy if you want it gone. `claude-session-archive secrets` finds them for you.
- Your cloud provider can read what you store there unless you use end-to-end encryption (for example iCloud Advanced Data Protection, or an encrypted folder such as Cryptomator).
- Anyone who can write to the archive's `data/` files can change what the indexes say, but not run code: the tool only reads JSON and Markdown from there, and escapes it before writing the indexes. Links are only written for `https://claude.ai/` addresses.

What else the archive reveals, beyond the conversations themselves:

- `LAST-SYNC.txt`, the `Where` column and `all-sessions/computers/` name your computers (computer names often include your name).
- Session links identify sessions in your claude.ai account. Treat them like the rest of the archive: private.
- `SESSIONS.md` and the folder names under `all-sessions/projects/` contain the full paths of the folders you worked in, which can include your username and client or project names.
- The copies include subagent transcripts and `tool-results` files: full contents of files that were read and full output of commands that were run.
- **Keeping transcripts forever is itself a trade-off.** With `cleanupPeriodDays` at 36500, a secret pasted into a session also stays in `~/.claude` on your computer indefinitely, not only in the archive.
- Digests (`digest`) contain your messages (up to 1500 characters each), including anything you pasted. Handing them to a new Claude session to summarise sends that text to the model provider again and records it in the new session's transcript, which is archived too.
- An export from claude.ai (`export-claude-ai.js`) is the same kind of content as a transcript, in a file in your Downloads folder. Once `import` has filed it, delete the export.
- `SECRETS.md`, and the scan cache `secrets-cache.json` in the tool's config folder, never contain a credential: only its kind, a masked form (a password shows only its length), a keyed fingerprint and the files it is in. That is still enough to tell someone where to look, so keep them as private as the rest of the archive.
- The settings backups the installer writes (`~/.claude/settings.json.bak-<timestamp>`, the newest 3 kept, readable by you only) contain whatever your settings contain, including any `env` tokens.

The archive, indexes, config and log are created readable by you only (permissions 0600 / 0700 on macOS and Linux). An independent review of the code, its findings, and the tests that pin each fix are described in [securityaudit.md](securityaudit.md); run them with `python3 -m unittest discover -s tests`.

## Sessions on claude.ai

claude.ai keeps its own copy of two kinds of session:

- **Cloud sessions**, started on [claude.ai/code](https://claude.ai/code) or the mobile app. They run on Anthropic's servers, so no computer of yours has a transcript.
- **Remote Control sessions**: local sessions you could also reach from claude.ai or your phone. Your computer has the transcript, but only until Claude Code deletes it, and claude.ai's copy can outlast it.

According to [Anthropic's data usage page](https://code.claude.com/docs/en/data-usage), what claude.ai keeps follows your account's retention policy, so don't rely on it lasting. There is no documented export, so this folder includes [`export-claude-ai.js`](export-claude-ai.js), a short script you run yourself in your browser while signed in to claude.ai. It reads the same addresses the claude.ai page loads, with your own sign-in, and downloads every session as compressed files. The tool itself still makes no network requests.

1. Open https://claude.ai/code, open the developer console (Chrome: View > Developer > JavaScript Console), read `export-claude-ai.js`, paste it in and press Enter. Only paste the file from this repository: a modified copy would run with your claude.ai sign-in. Save each file it downloads; after the first, Chrome asks whether claude.ai may download multiple files, and the rest are held back until you click **Allow**. The console says how many files to expect.
2. File them into the archive, naming the files the script saved:

   ```sh
   claude-session-archive import ~/Downloads/claude-ai-sessions-2026-10-11-part1.jsonl.gz ~/Downloads/claude-ai-sessions-2026-10-11-part2.jsonl.gz
   ```

   Name them rather than using a wildcard such as `claude-ai-sessions-*`: anything else in Downloads with a matching name, including a file a website saved there, would be imported too. `import` lists the title of every session it adds, so check that list.

   Each session becomes `all-sessions/cloud/session_<id>.json`. A saved copy is only replaced by one that contains every event it already has; a copy with different content is kept beside it under `all-sessions/cloud/versions/`, so importing never loses anything. A record that does not look like an export, a file it cannot read, or a session larger than 256 MB is reported and skipped, never a reason to stop.
3. Delete the downloaded files.

The index then shows:

- **cloud sessions** under `Cloud · <how it was started> · <repo> (<branch>)`;
- **recovered sessions**, Remote Control sessions whose local transcript is gone, under `Recovered from claude.ai · Terminal, Remote Control · <repo>`;
- for a session your computer still has, nothing new in the index; `SESSIONS.md` notes that a claude.ai copy was also saved.

`digest` covers imported sessions too, so they can be summarised like any other; their summaries go in `data/summaries.json` under their `session_<id>`. A claude.ai copy holds the conversation and tool calls as claude.ai recorded them, which may not be everything the local transcript had.

To add details by hand, or to list a cloud session you have not exported, use `data/cloud.json`. Its fields fill in or override what the saved copy says:

```json
{ "sessions": [
    { "title": "Pricing page ideas", "url": "https://claude.ai/code/session_01ExampleOnly",
      "date": "2026-09-30", "last": "2026-10-02", "group": "Marketing",
      "started_from": "Android app", "repo": "you/your-repo", "branch": "claude/pricing-ideas",
      "first_request": "What should the pricing page say?",
      "summary": "What was asked, what was done, how it ended.", "keep": "One thing worth remembering.", "kind": "Research" }
] }
```

Only `title` is required. Only `https://claude.ai/` links are accepted.

## Scanning for secrets

```sh
claude-session-archive secrets          # scan now and write SECRETS.md in the archive
claude-session-archive secrets --fail   # also exits 1 if anything is found
```

**Every sync also runs it**, so `SECRETS.md` stays current without you doing anything, and `INDEX.md` opens with a line saying how many credential-shaped values the archive holds. The first scan reads everything (minutes on a large archive, in the background); after that only new and grown files are read, using a cache in the tool's own config folder (`secrets-cache.json`) that holds fingerprints, masked forms and line numbers, never a value. The cache is saved as the scan goes (at least once a minute), so an interrupted scan resumes where it stopped instead of starting over, and it resets itself whenever the patterns change. In a terminal the scan shows its progress (`file 37/106, 412/1523 MB`), and the sync log records how many new files it read and how long that took. `import` scans the copies it has just filed before it returns, so the next sync has nothing left to read. A saved claude.ai copy is a single line of JSON, often tens of MB, so it is read in pieces cut between its events, which lets the scan skip most of it. A failed scan never stops a sync.

It reads every transcript, memory note and saved claude.ai copy in the archive and looks for credentials with a recognisable shape: private keys; Anthropic, OpenAI, Google, Stripe (live), npm, GitLab and GitHub tokens; AWS access key ids; Slack tokens and webhooks; database URLs with a password; JSON Web Tokens; and `password = "..."`-style assignments. Obvious placeholders (`your-api-key`, `xxxxxx`, `${VAR}`, `example`, `test`) are skipped.

`SECRETS.md` opens with a summary (how many values, how many need attention), then:

- **⚠️ Needs attention**: findings that could be live credentials, each with where it came from (the database user and host, or the variable name), its masked value, how many places and sessions hold it, a link to the first transcript it appears in (relative to the archive folder, so it opens in VS Code, Obsidian or any local Markdown viewer) and to the session on claude.ai, and its fingerprint.
- A folded section (a `<details>` toggle that GitHub, VS Code and Obsidian show as a click-to-expand) with everything that is probably not a live credential, and why: a sign-in token that expires within hours, a local or example database host (`localhost`, `127.x`, `*.local`, `*.test`, `example.com`), a password only ever seen next to the words test, fixture, seed or example in the message, or your own verdict. A key in a live format (Stripe, AWS, GitHub, Anthropic, OpenAI and the like) is never folded because of nearby wording: only your verdict can fold it.

Each value is **masked**: passwords (assignments and the password in a database URL) show only their length; keys show the first few characters of their public format prefix and, if long, the last two. It never contains the value itself, and the tool never changes a transcript unless you run `redact`.

**Your verdicts.** The automatic sorting is a guess. Record what you found in `data/secrets-triage.json`, and it moves the row and is shown as the reason, on every later scan:

```json
{ "3f2a9c1b7d4e": { "status": "rotated", "note": "Stripe key rotated 2026-10-12" },
  "9e8d7c6b5a41": { "status": "test", "note": "fixture in tests/payments" } }
```

`status` is one of `real` (keeps it under Needs attention), `dev`, `test`, `example`, `rotated`, `not-secret`.

The fingerprint is an HMAC keyed with a random value kept in `data/.secrets-salt`, so it identifies a value across scans and computers without letting someone who has only `SECRETS.md` test guesses against it. Keep the salt with the archive; deleting it changes every fingerprint, so the entries in `data/secrets-ignore.json` stop matching.

What it does not scan: your own `data/` files, `digest` output, and the generated `INDEX.md` and `SESSIONS.md`, which quote each session's first request (up to 160 characters, so a credential pasted at the very start of a session is shown there; they are rebuilt from the transcripts, so a `redact` clears them too). `digest` copies the text of your messages, credentials included, so run `secrets` before handing digests to a model.

For each real credential, **rotate it** with the service that issued it: it is stored in plain text in every copy of the archive and in `~/.claude` on the computer it came from, so deleting it from one file does not make it secret again. For a match that isn't a secret (a test value), add its fingerprint to `data/secrets-ignore.json`:

```json
{ "ignore": ["3f2a9c1b7d4e"] }
```

### Replacing a credential with a descriptor

Once a credential is rotated you may still not want it sitting in plain text in every copy of the archive. `redact` replaces it, by fingerprint, with a descriptor such as `<secret removed: Database URL with password, app@db.example-corp.net, 3f2a9c1b7d4e>`:

```sh
claude-session-archive redact 3f2a9c1b7d4e            # dry run: says what would change
claude-session-archive redact 3f2a9c1b7d4e --apply    # really change the files
```

- **Exactly the value, nothing else.** It is replaced at the position the scan found it: if the password is `postgres`, the word "postgres" elsewhere in the transcript is untouched. A private key is replaced whole, header to footer, even when a transcript stores it with escaped line breaks. Line endings, permissions and timestamps are kept.
- **Here and in the archive, together.** It changes the archive and this computer's `~/.claude/projects`. A transcript written to in the last 10 minutes belongs to a running session and is skipped, together with its archive copy; run the command again later.
- **It stays redacted.** The fingerprints you redact are recorded (without values) in `data/redacted.json`, and every later sync and import re-applies them to whatever it copies: another computer's unredacted transcript, or a newer export from claude.ai, cannot bring the value back into the archive. Other computers' own `~/.claude` still hold the original until you run `redact` there too, and claude.ai keeps its own copy of cloud and Remote Control sessions.
- **It tells you what it could not do.** If the same text also appears in a form the scan does not recognise (for example a command-line flag such as `mysql -p...`), it says how many times and in which file, without printing it. A file that is not UTF-8 text, a fingerprint that is not found and a file it could not write are each reported, and the exit code is 1.
- **It never breaks a transcript.** A line that would stop being valid JSON is never written; that file is reported and left as it was.
- **There is no undo** and no backup, because a backup would keep the secret. Rotate first; redact second. Your cloud drive may still hold earlier versions of a file in its version history or trash.

These are pattern matches, not proof. Expect false positives, most often test fixtures: if you work on code that handles credentials, its tests are full of realistic fake keys. Look at the session before rotating anything, and silence fixtures with the ignore list. Do not treat an empty report as a guarantee. A credential with no recognisable shape (a plain password typed into a sentence) will not be found.

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
