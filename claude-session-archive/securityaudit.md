# Security and privacy audit: claude-session-archive 1.0.0

Date: 2026-10-11. Scope: `claude_session_archive.py`, `install.sh`, `install.ps1`, as published in this folder.

## Method

1. **Independent adversarial review** of the pre-release code (read-only), covering: what can leave the machine; path traversal and symlinks in the copy; shell and command injection; file permissions; safety of editing `~/.claude/settings.json`; correctness under concurrency, offline drives and malformed input; Python 3.8 compatibility; Windows and POSIX-shell specifics. The reviewer reproduced each finding against a throwaway home folder and never touched a real configuration.
2. **Every finding fixed**, then pinned by an automated test in [`tests/test_claude_session_archive.py`](tests/test_claude_session_archive.py). Each test runs the real command-line tool in a subprocess against a throwaway home folder, Claude config folder and archive.
3. **Mutation check.** To show the tests guard something, eight fixes and checks were reverted one at a time in a scratch copy and the suite re-run. Each revert turned tests red (results below), and the restored code is green.

## Results

| Check | Result |
|---|---|
| Test suite, Python 3.9.6 (macOS system Python) | 82 tests, all pass |
| Test suite, Python 3.13 (Homebrew) | 82 tests, all pass |
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

## Second review: 1.1.0 (import, saved sessions, secrets scan, export script)

Date: 2026-10-11. Scope: everything added after the first review, at commit `a189197`: `import`, indexing of sessions saved from claude.ai, the `secrets` command, `digest` over saved copies, and `export-claude-ai.js`. Same method as the first review: an independent reviewer read the code (read-only), reproduced each finding by running the real CLI against throwaway folders with fake secrets, and suggested fixes. Every fix below is pinned by a test, and every test was shown failing with its fix reverted (results at the end of this section).

### Medium

| # | Finding | Fix (1.2.0) | Test |
|---|---|---|---|
| F1 | Text from an imported export (title, repository, branch, first request, and dates, which were written unescaped) could put Markdown links, remote images and raw HTML into `INDEX.md` and `SESSIONS.md`, breaking "links point only to claude.ai". Dates from local transcripts had the same gap. | `clean()` now escapes backslash, `[`, `]` and backtick everywhere, and writes `://` and `www.` in quoted text as HTML entities, so a bare URL looks the same but is never auto-linked. Dates are accepted only as `YYYY-MM-DD`. The only links left are the ones the tool builds, to claude.ai, and `link()` re-checks them. | `test_f1_no_links_images_or_html_from_imported_text` |
| F2 | One malformed saved record (`config.outcomes` not a list, or JSON nested 100,000 deep) raised an uncaught error: every later sync crashed, silently, because the hook is asynchronous. | Reading a saved record is wrapped: an unreadable one is skipped and logged; types are checked before iterating. | `test_f2_malformed_saved_record_does_not_stop_the_index` |
| F3 | "Replace only when the new copy has more events" lost data: a junk record with more events replaced a complete copy, and an unreadable copy was overwritten by anything. | A copy is replaced only when every one of its events (by event id) is in the new one. Any other different copy, and anything arriving for an unreadable copy, is kept beside it under `all-sessions/cloud/versions/`. | `test_f3_a_different_copy_is_kept_beside_never_over`, `test_f3_a_grown_copy_replaces` |
| F4 | The scan missed credentials systematically: any key right after a JSON `\n` or `\t` escape (the letter defeated the boundary checks), and `DB_PASSWORD=`, `SECRET_KEY=`, unquoted `PASSWORD=` and YAML `password:` forms. | JSON escapes are turned into spaces before matching; a new pattern covers environment and YAML assignments, ignoring values that are plain words or dotted names (`process.env.X`, `string`). | `test_f4_keys_after_json_escapes_and_env_assignments_found` |
| F5 | Every private key of one type had the same fingerprint (only the header was hashed), so ignoring one test key silenced every real one. | The fingerprint covers the key body; a header with no key after it is a mention, not a finding. | `test_f5_each_private_key_has_its_own_fingerprint` |
| F6 | The README's `import ~/Downloads/claude-ai-sessions-*` wildcard would import any similarly named file a website dropped into Downloads (combined with F1 and F2: injected entries and a stopped sync). | The README now names the files; `import` refuses records without the structure the export script writes and lists the title of every session it adds. F1 and F2 remove the rest of the impact. | `test_f6_records_without_export_structure_are_skipped` |

### Low

| # | Finding | Fix (1.2.0) | Test |
|---|---|---|---|
| F7 | `SECRETS.md` alone was an offline guess-checker: an unsalted 48-bit SHA-256 prefix plus the mask recovered a weak database password from a list of guesses in a millisecond, and a 13-character password showed 6 characters. | Fingerprints are HMAC-SHA-256 keyed with a random 256-bit salt kept in the archive (`data/.secrets-salt`), stable across computers. Passwords show only their length; keys show a short public prefix. | `test_f7_fingerprints_are_keyed_and_short_values_barely_shown` |
| F8 | `import` crashed with a traceback on a truncated gzip, non-UTF-8 text, a byte-order mark, an indented `{"sessions": [...]}` file or a folder, after filing part of it and without rebuilding the index. | Each file is read defensively: problems are reported per file, the sessions before a fault are kept, the next file is processed, and the index is always rebuilt. Byte-order marks and indented JSON are accepted. | `test_f8_bad_files_report_a_problem_not_a_traceback` |
| F9 | Memory was unbounded: a 0.29 MB compressed file grew to 1 GB in memory. | Lines are read with a 256 MB cap per session record (the largest seen in practice is about 10 MB); a longer record is reported and skipped. | `test_f9_oversized_record_is_refused` |
| F10 | If `all-sessions/cloud` was a symlink, `import` wrote outside the archive. | `import` refuses to write through a symlinked `all-sessions` or `all-sessions/cloud`, and skips any destination that is not a regular file. | `test_f10_symlinked_cloud_folder_refused` |
| F11 | `uninstall` left `saved-cache.json` (session titles and first requests) behind. | All caches are removed. | `test_f11_uninstall_removes_every_cache` |
| F12 | About 4 s per MB on content made of thousands of JWT-shaped tokens. | Profiling showed the cost was not the regex but the overlap check between matches, which compared each match with every earlier one. It now uses a sorted list and binary search: the reviewer's input went from 3.2 s to 0.13 s per MB with the same results. JWTs also start only at a token boundary. | `test_f12_many_matches_on_one_line_stay_fast` |

### Smaller items

| Finding | Outcome |
|---|---|
| A `cloud.json` link with `?query` or `#fragment` listed the session twice | Fixed: the id is taken from the path. `test_cloud_url_with_query_is_listed_once` |
| The placeholder filter looked at the whole database URL, so a user named `test_runner` hid a real password | Fixed: only the password is checked and fingerprinted. Covered by `test_finds_each_kind_and_never_writes_the_value`, which now uses that user name |
| Every non-cloud saved session was labelled Remote Control | Fixed: the label follows the session's recorded kind |
| A backtick could break the table layout | Fixed (escaped); asserted in the F1 test |
| `import` took no lock against a running sync | Fixed: it takes the sync lock |
| Cached rows were trusted when `digest` built a path from them | Fixed: the saved file name is re-validated |
| `digest` copies credentials into its output; `data/` and digests are not scanned | Accepted and documented in the README (run `secrets` before handing digests to a model) |
| "First seen in" is always line 1 for a saved copy, which is one long line | Accepted; the file still identifies the session |
| The placeholder words can hide a real value containing them (`Contest2024!`) | Accepted: the trade against noise is documented ("pattern matches, not proof") |

### Found while checking the fixes against the real archive

Re-scanning the author's archive after the fixes, and comparing with the previous report, found two more problems; both are fixed and pinned:

| Finding | Fix | Test |
|---|---|---|
| Google API keys dropped from 3 to 0. The base64 boundary added in 1.1.0 (to stop false hits inside image data) also refused a key after `=`, which is exactly how keys appear in URLs (`?key=AIza...`). This was a false negative introduced by our own earlier fix, and no test covered the common form. | `=` removed from the boundary; base64 padding never precedes more key characters. | `test_google_key_in_a_url_query_is_found` |
| The new F4 assignment pattern raised password findings from 5 to 23, most of them noise: Woodpecker `from_secret: <name>` references, placeholder phrases (`BETTER_AUTH_SECRET=replace-with-...`) and empty assignments followed by the next word. | `from_secret` excluded; `KEY=` must be followed directly by the value (YAML `key: value` still allowed); an assignment value must contain a digit. A real password made only of letters is now missed; the README says pattern matching is not a guarantee. | `test_assignment_noise_is_not_reported` |

The lesson for the next change to the patterns: compare the report before and after on a real archive, not only the tests. Both problems were visible only as a change in the counts.

### export-claude-ai.js

The reviewer read it but did not run it, and confirmed it only makes same-origin GET requests to claude.ai and only writes downloads to disk: no other host, no POST, no storage. Changes: it releases each file's memory after saving it, says how many files to expect, and explains Chrome's "download multiple files" prompt, which on the author's first run held back seven of eight files until it was allowed (they arrived intact afterwards). The README tells users to paste only the file from this repository, since a modified copy would run with their claude.ai sign-in. The reviewer doubted that `response_shape` is the right field; it is: the author's run returned real titles, dates and repositories through it. The script still has no automated test, and the endpoints it reads are undocumented, so it can stop working without notice.

### Mutation check (second review)

Each fix was reverted alone in a scratch copy and the suite run: F1 bracket escaping, F1 URL defanging, F1 date validation, F2, F3, F4 escapes, F4 code-like values, F5, F7 keyed fingerprint, F7 password masks, F6 structure check, F6 title list, F8, F9, F10, F11, F12, the database-password value, the query-string id and the backtick escape all turned a test red. Two tests were found unable to fail on the first try and were rewritten until they could: F12 (the scan had already been served from the cache) and the backtick (no test touched it).

### Results

| Check | Result |
|---|---|
| Test suite, Python 3.13 and 3.9.6 | 82 tests, all pass |
| Real archive (240 sessions, 2,650 files, about 1.7 GB) | full scan about 4 minutes, then a sync in under a second |

## Third review: `redact` and the SECRETS.md triage (1.3.0, fixed in 1.4.0)

Date: 2026-10-12. Scope: `redact`, which rewrites transcripts irreversibly, and the triage added to `SECRETS.md`, at commit `87867fa`. Same method as before: an independent reviewer, read-only, reproducing every finding with the real CLI in throwaway folders and fake secrets, including a 20,000-case fuzz of the replacement offsets. Before this review, 1.3.0 had only the author's own review; the review found what that missed. None of the findings of the two earlier reviews had regressed.

### Findings and fixes

| # | Severity | Finding | Fix (1.4.0) | Test |
|---|---|---|---|---|
| R1 | **High** | A private key stored in a transcript has its line breaks as JSON `\n` escapes, so the pattern matched its first line only. `redact` replaced that line and left the rest (24 of 25 lines in the repro), and the next scan, seeing a header followed by a descriptor, stopped listing the key: the user was told it was gone. | The key is matched whole, header to the end of its body, across escaped or real line breaks (notes are scanned whole, not line by line), fingerprinted by its body without line breaks, and replaced whole. | `test_r1_whole_private_key_is_redacted` |
| R2 | Medium | Redaction stopped where the pattern stopped: `DB_PASSWORD=Hunter2024;Zq9!x8Lm` lost only `Hunter2024`. The same value in another form (`mysql -p...`) was untouched, and the scan then reported nothing. | Values may contain `, ; ( ) [ ] { }` (and anything but quotes inside quotes). After `--apply`, the remaining copies of each replaced value are counted per file and reported (never printed); ordinary words are not counted, so the check does not cry wolf. | `test_r2_value_with_punctuation_is_replaced_whole_and_other_forms_reported`, `test_leftover_check_ignores_an_ordinary_word` |
| R3 | Medium | A transcript skipped as in use still had its archive copy redacted; the next sync then filed the unredacted local copy beside it, and once the session grew, over it. Another computer's sync did the same. | The in-use skip covers the pair. Redacted fingerprints are recorded (no values) in `data/redacted.json`, and every sync re-applies them to whatever it copies into the archive. | `test_r3_in_use_transcript_skips_its_archive_copy_too`, `test_r3_sync_reapplies_redactions_to_whatever_it_copies` |
| R4 | Medium | `redact` set rewritten files' modification time to now, so a second run within 10 minutes saw them all as in use (leading into R3). An undocumented `redacted` triage status could then hide a value that came back. | Rewrites keep the file's timestamps and permissions; the `redacted` status is gone (the ledger in R3 replaces it). | `test_r4_redact_keeps_timestamps` |
| R5 | Medium | Importing a newer claude.ai export of a redacted session counted as an update and restored the original. | Every import re-applies the recorded redactions to what it writes. | `test_r5_import_reapplies_redactions` |
| R6 | Medium | The triage guess folded real credentials: "test" matched inside "latest" or "contest", and in session metadata before the message; every `*.internal` host and the bare names `db`, `host`, `database` counted as local. In the repro all five findings folded and the report said nothing needed attention. | Test words count only as whole words in the message text; only `localhost`, `127.x`, `*.local`, `*.localhost`, `*.test`, `*.example`, `*.invalid` and `example.com/net/org` are local; and a key in a live format is never folded for nearby wording, only by your verdict. | `test_r6_latest_or_internal_host_does_not_fold_a_real_credential`, `test_attention_first_others_folded_with_links` |
| R7 | Low | A non-UTF-8 file was silently not redacted ("replaced 0", exit 0), and an unknown fingerprint gave the same output. | Both are reported, with exit code 1. | `test_r7_unknown_fingerprint_is_reported` |
| R8 | Low | A write error during `--apply` stopped with a traceback, part-way through, without a summary or a rebuilt report. | Each file is handled separately: a failure is reported, the others are processed, and the summary, report and index are always produced. | `test_r8_a_write_failure_is_reported_and_the_rest_continues` |
| R9 | Low | `redact` converted CRLF and lone CR line endings to LF, changing bytes outside the value. | Files are read and written without newline translation. | `test_r9_line_endings_are_preserved` |
| R10 | Low | Session counts and "First seen in" were wrong for `versions/<computer>/` copies, and memory notes counted as an extra nameless session. | The session is derived from each real layout; memory notes belong to no session. | `test_r10_session_of_layouts` |
| R11 | Info | `INDEX.md`, `SESSIONS.md` and the index cache quote each session's first request (160 characters), credentials included, and are not scanned. | Documented in the README; a `redact` clears them because they are rebuilt from the redacted transcripts. | n/a |
| S3 | Info | If `data/.secrets-salt` had not synced yet to a computer, a scan created a new one, changing every fingerprint and breaking triage, ignore and redaction lists. | An archive that has been scanned before must already have its salt; otherwise the scan stops with a message (the sync itself carries on). | `test_s3_missing_salt_is_never_silently_replaced` |
| S4 | Info | `user@host` in the report could be rendered as an e-mail link. | Written with `@` as an HTML entity. | `test_attention_first_others_folded_with_links` |
| S1, S2 | Info | A Claude Code write between `redact` reading and replacing a file could be lost if the session had been idle 10 minutes; a cloud drive's version history keeps the file as it was. | Accepted and documented (the in-use window, and "your drive may keep earlier versions"). | n/a |

Mutation check: each of R1, R2 (both parts), R3 (both parts), R4, R5, R6 (all three parts), R7, R8, R9, R10, S3 and S4 was reverted alone and turned its test red.

### Found by re-scanning the author's archive after the fixes

As after the second review, the report was compared before and after the fixes on the real archive, and three more problems surfaced, all caused by the fixes themselves:

| Finding | Fix | Test |
|---|---|---|
| Widening values for R2 (so `;` and `,` no longer cut a password short) let a match run through brackets: three SOPS-encrypted values (`ENC[AES256_GCM,data:...]`, safe to store) and a piece of minified JavaScript were reported as passwords of 58 to 163 characters. | Brackets, braces and parentheses end a value again (`;` and `,` still do not, and a trailing one is dropped); `ENC[` is a placeholder, which matters for the quoted form. | `test_encrypted_and_code_values_are_not_secrets` (each of the two rules shown necessary by mutation) |
| The new leftover check (R2) warned that a redacted local password "still appears 26,886 times": the password is an ordinary word. | Only values with letters and digits, at least 8 characters, are searched for elsewhere; descriptors are excluded. | `test_leftover_check_ignores_an_ordinary_word` |
| The speed tests measured elapsed time, so they failed when the machine was busy (load average 84 at the time) and could pass a slow version on a fast machine. | They measure the CPU time the tool uses. The quadratic overlap check from the second review now uses 67 s of CPU against a 5 s limit. | `test_f12_many_matches_on_one_line_stay_fast`, `test_long_lines_stay_fast` |

### End-to-end test on real data

Run by the author on copies of real transcripts, never the originals, in a sandbox with its own home and archive folders, deleted afterwards. The target was a local development database password that is also an ordinary word, the hardest case for "replace exactly the value": it appears 1,043 times as a credential in 103 transcripts and saved claude.ai copies (159 files counting each computer copy), and its text appears some 27,000 times in all.

| Check | 1.3.0 | 1.4.0 |
|---|---|---|
| Dry run and `--apply` report the same occurrences and files | 1,043 in 159 files, both | same |
| Putting the value back in place of every descriptor reproduces each original file byte for byte | all 159 identical | all 159 identical |
| JSON lines that stopped parsing | 0 | 0 |
| Other uses of the same word left untouched | yes (count unchanged) | yes |
| A sync afterwards brings the value back (copies, `versions/`) | no | no |
| The rescan no longer lists it | yes | yes |
| `data/redacted.json` records it | (did not exist) | yes |
| Leftover warning | (did not exist) | first version warned about every use of the ordinary word; fixed so ordinary words are not counted |

Not covered: whether Claude Code can resume a redacted transcript (that would send it to the model); the timestamp comparison in this run was invalid, because the reference copy was made with `cp -R`, which does not keep timestamps, so timestamp preservation rests on its unit test; Windows.

## 1.5.0: shared settings and reminders (author's review)

Small and read-only, so reviewed by the author rather than independently; the checks that matter for it:

| Property | How it holds | Test |
|---|---|---|
| A reminder can never stop or slow a Claude Code session | `remind` only reads two small files, has a 10-second timeout in the hook, catches every exception and always exits 0 | `test_remind_never_fails`, `test_remind_survives_an_unwritable_config_folder` (the catch-all shown necessary by mutation) |
| A hostile or broken `settings.json` changes nothing it should not | each value is type- and range-checked against a fixed list; anything else falls back to its default; unknown keys are ignored; the tool never writes the file after creating it once | `test_reminders_off_and_bad_settings_fall_back_safely`, `test_settings_are_never_overwritten` |
| The reminder does not nag | at most once a day, silent when nothing is due, and switchable off | `test_at_most_once_a_day`, `test_reminds_when_never_or_overdue_and_not_when_recent` |
| Still no network access | settings and reminders read local files only; `test_no_network_or_subprocess_imports` still passes | |

Every behaviour above was reverted alone and turned its test red. Checked with Claude Code 2.1.296: a synchronous SessionStart hook's JSON output, including `systemMessage`, is received and recorded as successful (`hook_response` event). That the message appears in the interactive interface rests on Claude Code's documentation, to be confirmed by the first real reminder.

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
