# Security and privacy audit: claude-session-archive 1.0.0

Date: 2026-10-11. Scope: `claude_session_archive.py`, `install.sh`, `install.ps1`, as published in this folder.

## Method

1. **Independent adversarial review** of the pre-release code (read-only), covering: what can leave the machine; path traversal and symlinks in the copy; shell and command injection; file permissions; safety of editing `~/.claude/settings.json`; correctness under concurrency, offline drives and malformed input; Python 3.8 compatibility; Windows and POSIX-shell specifics. The reviewer reproduced each finding against a throwaway home folder and never touched a real configuration.
2. **Every finding fixed**, then pinned by an automated test in [`tests/test_claude_session_archive.py`](tests/test_claude_session_archive.py). Each test runs the real command-line tool in a subprocess against a throwaway home folder, Claude config folder and archive.
3. **Mutation check.** To show the tests guard something, eight fixes and checks were reverted one at a time in a scratch copy and the suite re-run. Each revert turned tests red (results below), and the restored code is green.

## Results

| Check | Result |
|---|---|
| Test suite, Python 3.9.6 (macOS system Python) | 45 tests, all pass |
| Test suite, Python 3.13 (Homebrew) | 45 tests, all pass |
| Python 3.8 syntax (`ast.parse(feature_version=(3, 8))`) | pass |
| `sh -n install.sh` | pass |
| Hook format accepted by Claude Code 2.1.296 (a real headless session with an exec-form `SessionStart` hook, both blocking and `async`) | both hooks ran |
| End-to-end install, sync, re-install and uninstall on macOS (author's machine, real archive of 59 sessions) | pass |
| Where-it-ran column on the same archive (59 local sessions: 55 Terminal with Remote Control, 2 Desktop app, 1 Terminal, 1 SDK; 6 cloud sessions from `data/cloud.json`) | pass |
| **Windows (`install.ps1`, `.cmd` shim, PATH edit)** | **not executed**: reviewed only, no Windows machine was available. Treat as untested until run there |
| Linux | not executed; same code paths as macOS except the installer's package-manager branch |

Mutation check (fix reverted, suite re-run):

| Reverted fix | Tests that failed |
|---|---|
| Escaping `<` `>` in titles and summaries | `test_title_cannot_inject_markup_and_index_is_stable` |
| "Only replace an archived transcript that has grown" | `test_smaller_local_transcript_never_replaces_archive_copy`, `test_changed_memory_note_kept_beside_not_over` |
| Refusing unsupported `settings.json` shapes before changing anything | `test_bad_settings_shapes_refused_before_any_change` (2 cases) |
| Pruning only the backups this tool created | `test_backups_capped` |
| Accepting only `https://claude.ai/` links from `data/cloud.json` | `test_cloud_sessions_listed_and_bad_links_dropped` |
| Accepting only a well-formed Remote Control id (`cse_` plus letters and digits) | `test_malformed_remote_control_id_not_linked` |
| Escaping `[` `]` in a linked title | `test_where_shows_computer_app_and_remote_control_link` |
| Each computer listing only its own sessions (not the whole archive) | `test_sessions_from_another_computer_name_it` |

## Findings and fixes

Severity is as the reviewer rated it. "Test" names the test that now pins the fix.

### High

| # | Finding | Fix | Test |
|---|---|---|---|
| H1 | Text from session titles and prompts was written into `INDEX.md` unescaped. A title containing the generated-region marker made the file duplicate part of itself on every sync; a title with a newline injected Markdown headings. | Every emitted string is collapsed to one line with `<`, `>` and `\|` escaped. `INDEX.md` is now fully generated, with no markers to confuse. | `test_title_cannot_inject_markup_and_index_is_stable` |
| H2 | `INDEX.md` mixed generated text with hand-written notes and was rewritten in place. An interrupted write could truncate it, and two computers syncing close together could overwrite each other's notes. | Hand-written notes moved to `data/curated.md`, which the tool only reads. Every generated file is written to a temporary file and atomically renamed into place. | `test_curated_notes_survive_every_sync` |
| H3 | "Never deletes" was not true: a local copy of a different size replaced the archived one, even when smaller (a truncated transcript), and memory notes on two computers overwrote each other. | An archived transcript is replaced only when the local copy has grown (transcripts are append-only). Any other differing version is kept beside it under `all-sessions/versions/<computer>/`. | `test_smaller_local_transcript_never_replaces_archive_copy`, `test_grown_transcript_replaces_archive_copy`, `test_changed_memory_note_kept_beside_not_over` |

### Medium

| # | Finding | Fix | Test |
|---|---|---|---|
| M1 | Unexpected `settings.json` shapes (`"hooks": []`, a top-level array, odd hook entries, a non-number `cleanupPeriodDays`) crashed install, status or uninstall, after some files were already written (half install). | Settings are read and validated before anything changes; unsupported shapes are refused with a message; `null` is treated as empty; odd entries inside valid shapes are ignored. | `test_bad_settings_shapes_refused_before_any_change`, `test_null_hooks_treated_as_none`, `test_odd_but_valid_entries_do_not_crash` |
| M2 | Rewriting `settings.json` replaced a symlinked file (dotfiles managers) with a plain copy, widened its permissions, escaped non-ASCII text, refused a file with a byte-order mark, and left unlimited backups. | The symlink target is updated in place; permissions are preserved; non-ASCII is kept; a byte-order mark is accepted; backups are created owner-only and only the newest 3 *of the tool's own* are kept. | `test_symlinked_settings_and_mode_preserved`, `test_non_ascii_preserved`, `test_invalid_json_refused_and_bom_accepted`, `test_backups_capped` |
| M3 | On a computer where Claude Code had never run, the first sync failed, so install failed and skipped the PATH step. | No transcripts is a successful empty sync; a failed first sync no longer fails the install. | `test_fresh_machine_without_transcripts_installs` |
| M4 | A relative `--archive` path was stored as typed, then resolved against whatever folder a later session started in. | Stored as an absolute path. | `test_relative_archive_path_stored_absolute` |
| M5 | Running `install` from the installed command crashed (copying the script onto itself). | Copy skipped when source and destination are the same file. | `test_install_from_installed_copy` |
| M6 | On a Mac without the Command Line Tools, `install.sh` ran the placeholder `/usr/bin/python3`, which pops up an install dialog. | The placeholder is only run once `xcode-select -p` reports the tools installed. | manual review |
| M7 | The hook stored the exact interpreter path, which can be a versioned Homebrew path removed by the next upgrade or a deleted virtual environment, so syncing stopped silently. | Installers pass the stable path they found (for example `/opt/homebrew/bin/python3`); `status` checks that the hook's interpreter and script exist and says BROKEN if not; a warning is printed inside a virtual environment. | `test_status_reports_runnable_hook` |
| M8 | A malformed `data/*.json` crashed every sync, or (invalid JSON) silently rendered every summary as missing. | Invalid JSON stops the rebuild with a message and leaves the previous indexes untouched; wrongly typed entries are skipped and logged. | `test_bad_data_files_stop_rebuild_and_keep_previous_index`, `test_wrong_typed_data_entries_ignored` |
| M9 | Every sync re-read every transcript (expensive on online-only cloud folders), and concurrent session starts could run several syncs at once. | Per-file cache keyed by size and modification time; a lock file; the "last sync" stamp set at the start. | `test_if_stale_skips_and_running_lock_skips` |
| M10 | Windows: the `.cmd` shim was written in UTF-8 (breaks non-ASCII user names); editing the user PATH through .NET flattens `%VARIABLE%` entries; a trailing backslash in `-Archive` was mangled by PowerShell 5.1; Microsoft Store Python may redirect writes under `%APPDATA%`. | Shim refers to its own folder (`%~dp0`) and is written in the console code page; PATH is read and written as an unexpanded `REG_EXPAND_SZ` with a change broadcast; trailing separators trimmed; Store Python skipped. | not executed (no Windows machine) |

### Low

| Finding | Fix | Test |
|---|---|---|
| Created files and folders were readable by other users (0644 / 0755). | `umask 077`: owner-only. | `test_created_files_private` |
| A symlink planted inside the archive was written through. | Destination symlinks are skipped. | `test_symlink_planted_in_archive_not_written_through` |
| The POSIX command shim interpolated paths unquoted (a path containing `$(...)` would execute). | Paths quoted with `shlex.quote`. | `test_shim_paths_are_shell_quoted` |
| A colon in an automatically generated title created spurious groups. | Only titles you set yourself (`/rename`) create groups. | `test_custom_title_prefix_groups_but_ai_title_colon_does_not` |
| Uninstall could delete a `~/.local/bin/claude-session-archive` belonging to something else. | Only removed if it is this tool's shim. | `test_foreign_shim_left_alone` |
| `install.sh`: `--uninstall` only as the first argument; fish shell told PATH was added when it was not; `cd` not `CDPATH`-safe. | All fixed. | manual review |
| `digest --cap 0` returned everything. | Minimum 1000. | `test_digest_and_cap` |

### Added after the review: where each session ran

The index gained a column saying which computer and app ran each session, a claude.ai link for Remote Control sessions, and a hand-maintained list of cloud sessions (`data/cloud.json`). This adds new text and new links to the generated files, so it was checked against the same threat as H1 (anything written into the archive turning into markup or a misleading link):

| Input | Risk | Handling | Test |
|---|---|---|---|
| `url` in `data/cloud.json` | a link to another site, `javascript:`, or text that closes the Markdown link and opens a new one | linked only if the whole value matches `https://claude.ai/` followed by URL-safe characters with no `)`, `]` or spaces; otherwise shown unlinked and logged | `test_cloud_sessions_listed_and_bad_links_dropped` |
| `bridgeSessionId` in a transcript | same, via a crafted transcript | linked only if it is exactly `cse_` plus 8 to 64 letters or digits; the link is built by the tool, not copied | `test_malformed_remote_control_id_not_linked` |
| Session title used as link text | `]` ending the link label early | `[` and `]` escaped, on top of the H1 escaping | `test_where_shows_computer_app_and_remote_control_link` |
| `repo`, `branch`, `started_from`, `first_request` in `data/cloud.json` (1.0.1) | markup in the Where column or SESSIONS.md | escaped like every other string | `test_cloud_sessions_listed_and_bad_links_dropped` (a branch containing `<b>`; shown failing with the escaping removed) |
| Computer name in `all-sessions/computers/*.json` | markup in the name; a corrupt file breaking the sync | escaped like every other string; an unreadable file is skipped and logged | `test_sessions_from_another_computer_name_it` |

The tool still opens no network connection: links are written into the Markdown, never fetched.

### Added in 1.1.0: import from claude.ai, and a secrets scan

Both handle the most sensitive data the tool touches: a whole export of claude.ai sessions, and the credentials inside transcripts. Checked against two threats: an export file turning into a write outside the archive or a loss of data, and the secrets report becoming a leak itself.

| Input or output | Risk | Handling | Test |
|---|---|---|---|
| Session `id` in an export file | becomes a file name: `../../` path traversal | must match `session_` (or `cse_`) plus 8 to 64 letters and digits, otherwise skipped and counted | `test_import_gzip_jsonl_recovers_lost_sessions_into_the_index` |
| Re-importing an older export | a shorter copy replacing a fuller one | replaced only when the new copy has more events | `test_import_never_replaces_a_fuller_copy` |
| A symlink planted at the destination | writing through it | skipped | (same handling as the sync; reviewed) |
| A session both local and saved | listed twice, double counted | the saved copy attaches to the local row | `test_local_remote_control_session_is_not_listed_twice` |
| Titles, repositories, first requests from claude.ai | markup in the indexes | escaped like every other string; links built only from validated ids | (same functions as H1) |
| `SECRETS.md` and the command's output | printing the credentials it found | only `mask()` output (four leading and two trailing characters, length) and a 12-digit SHA-256 fingerprint are written; tested against the full values in both the report and stdout/stderr | `test_finds_each_kind_and_never_writes_the_value` |
| Placeholder values, hyphenated words | noise that trains people to ignore the report | placeholders skipped; OpenAI-shaped matches must look random and not follow a word character | `test_finds_each_kind_and_never_writes_the_value`, `test_hyphenated_words_are_not_keys` |
| Key shapes inside base64 data (images) | false Google API keys | a key must not follow or precede base64 characters | `test_key_shapes_inside_base64_are_not_keys` |
| Megabyte-long transcript lines | catastrophic regex backtracking | every quantifier bounded; each pattern only runs on lines containing its literal prefix | `test_long_lines_stay_fast` |

Mutation check, each reverted alone: masking (red), the placeholder filter (red, after a first version of the test was found unable to fail and was rewritten), the id check (red), the never-shrink rule (red), the local de-duplication (red), the word-boundary and randomness check (red), the base64 boundary on Google keys (red, after a first version of its test was again found unable to fail).

On the author's archive, after importing 181 sessions from claude.ai (2,650 files, about 1.7 GB), the scan takes about 4 minutes. Reviewing the high-value hits by their surrounding text showed them to be test fixtures (the scanner's own exposed-secret detector tests, AWS's published example key) and one match inside base64 image data, which led to the base64 boundary. Earlier, on 2,475 files: 37 distinct values found in about 2 minutes, after the false-positive fixes removed 7 (six hyphenated words read as OpenAI keys, and one well-known example password) and the duplicate counting was fixed. `export-claude-ai.js` is not covered by the Python tests; the same requests, run as an inline script, exported 183 sessions from a real account, but the published file itself has not been run end to end.

## Confirmed properties (checked, unchanged)

- **No network access and no other programs run by the tool.** It imports only standard-library modules for files, JSON, paths and time; no `subprocess`, `urllib`, `http`, sockets beyond reading the computer's own name. Pinned by `test_no_network_or_subprocess_imports`. The installers use the network only to install Python when it is missing (Homebrew, the Linux package manager, or `winget`).
- **The hook runs no shell** (exec form: interpreter path plus arguments), so it cannot be broken by spaces or quotes in paths.
- **Source symlinks are not followed**; file names come from the file system walk, so no path traversal.
- **Invalid `settings.json` is never written**: refused with nothing changed.
- **No personal data in the published files**: no names, e-mail addresses, home paths or session ids.

## Residual risks (accepted, documented in the README)

- The archive is a copy of your transcripts, which can contain secrets and customer data. Its safety is that of the folder you choose and of your cloud provider. Keep it private.
- Keeping transcripts forever (`cleanupPeriodDays` 36500) also keeps any pasted secret on your computer forever.
- Two computers editing the same `data/` file at the same moment can still conflict in the cloud drive; that file is yours and the tool never writes it.
- Windows behaviour is reviewed but untested.
- Cloud sessions (claude.ai/code) are out of reach of this tool.

## Re-running

```sh
python3 -m unittest discover -s tests -v
```
