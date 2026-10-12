"""Tests for claude_session_archive.py. Standard library only.

    python3 -m unittest discover -s tests -v

Every test runs the real CLI in a subprocess against a throwaway HOME, Claude config
folder and archive, so nothing on the machine running the tests is touched.
"""
import ast
import gzip
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "claude_session_archive.py"
POSIX = os.name != "nt"


def line(**rec):
    return json.dumps(rec) + "\n"


def transcript(title="A session", prompt="hello there", ts="2026-10-01T10:00:00Z", custom=True, extra=""):
    key = "customTitle" if custom else "aiTitle"
    return (line(type="user", timestamp=ts, cwd="/work/proj", message={"role": "user", "content": prompt})
            + line(type="assistant", timestamp=ts, message={"role": "assistant", "content": [{"type": "text", "text": "done"}]})
            + line(**{key: title}) + extra)


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.claude = self.home / ".claude"
        self.projects = self.claude / "projects" / "-work-proj"
        self.conf = root / "conf"
        self.drive = root / "drive"
        self.archive = self.drive / "Archive"
        for p in (self.projects, self.conf, self.drive):
            p.mkdir(parents=True)

    def run(self, *args, extra_env=None):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                   CLAUDE_CONFIG_DIR=str(self.claude), CLAUDE_SESSION_ARCHIVE_HOME=str(self.conf))
        env.pop("CLAUDE_SESSION_ARCHIVE_PYTHON", None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def install(self, *extra):
        return self.run("install", "--archive", str(self.archive), *extra)

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))

    def write_session(self, sid, text):
        (self.projects / f"{sid}.jsonl").write_text(text, encoding="utf-8")

    def close(self):
        self.tmp.cleanup()


class Base(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()


class InstallTests(Base):
    def test_install_sets_retention_hook_and_keeps_other_settings(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").write_text(json.dumps(
            {"theme": "light", "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
        self.e.write_session("s1", transcript())
        r = self.e.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.e.settings()
        self.assertEqual(s["theme"], "light")
        self.assertEqual(s["cleanupPeriodDays"], 36500)
        cmds = [h for e in s["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual(cmds[0]["command"], "echo mine")
        ours = cmds[1]
        self.assertTrue(ours["async"])
        self.assertEqual(ours["args"][1:], ["sync", "--if-stale"])
        self.assertTrue(Path(ours["command"]).is_absolute())

    def test_reinstall_is_idempotent_and_replaces_legacy_shell_hook(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        legacy = {"hooks": [{"type": "command", "command": "$HOME/.local/bin/claude-session-archive --if-stale >> log 2>&1"}]}
        (self.e.claude / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [legacy]}}))
        self.e.install()
        self.e.install()
        self.assertEqual(len(self.e.settings()["hooks"]["SessionStart"]), 1)

    def test_bad_settings_shapes_refused_before_any_change(self):
        for bad in ({"hooks": []}, {"hooks": "x"}, {"hooks": {"SessionStart": {}}}, []):
            with self.subTest(bad=bad):
                e = Env()
                try:
                    e.claude.mkdir(parents=True, exist_ok=True)
                    raw = json.dumps(bad)
                    (e.claude / "settings.json").write_text(raw)
                    r = e.install()
                    self.assertEqual(r.returncode, 1)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual((e.claude / "settings.json").read_text(), raw)
                    self.assertFalse((e.conf / "config.json").exists(), "half install left behind")
                finally:
                    e.close()

    def test_null_hooks_treated_as_none(self):
        for shape in ({"hooks": None}, {"hooks": {"SessionStart": None}}):
            with self.subTest(shape=shape):
                e = Env()
                try:
                    e.claude.mkdir(parents=True, exist_ok=True)
                    (e.claude / "settings.json").write_text(json.dumps(shape))
                    r = e.install()
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(len(e.settings()["hooks"]["SessionStart"]), 1)
                finally:
                    e.close()

    def test_odd_but_valid_entries_do_not_crash(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").write_text(json.dumps({"cleanupPeriodDays": "x", "hooks": {"SessionStart": [
            "x", {"hooks": [{"type": "command", "command": None, "args": [1]}]}]}}))
        r = self.e.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.e.settings()["cleanupPeriodDays"], 36500)
        self.assertEqual(self.e.run("status").returncode, 0)
        self.assertEqual(self.e.run("uninstall").returncode, 0)

    def test_invalid_json_refused_and_bom_accepted(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").write_text('{"a": 1,}')
        self.assertEqual(self.e.install().returncode, 1)
        (self.e.claude / "settings.json").write_bytes(b'\xef\xbb\xbf{"a": 1}')
        self.assertEqual(self.e.install().returncode, 0)
        self.assertEqual(self.e.settings()["a"], 1)

    def test_non_ascii_preserved(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").write_text(json.dumps({"note": "שלום"}, ensure_ascii=False), encoding="utf-8")
        self.e.install()
        self.assertIn("שלום", (self.e.claude / "settings.json").read_text(encoding="utf-8"))

    @unittest.skipUnless(POSIX, "POSIX permissions and symlinks")
    def test_symlinked_settings_and_mode_preserved(self):
        real = self.e.home / "dotfiles" / "settings.json"
        real.parent.mkdir(parents=True)
        real.write_text("{}")
        os.chmod(real, 0o600)
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").symlink_to(real)
        self.e.install()
        self.assertTrue((self.e.claude / "settings.json").is_symlink())
        self.assertEqual(json.loads(real.read_text())["cleanupPeriodDays"], 36500)
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o600)

    def test_backups_capped(self):
        self.e.claude.mkdir(parents=True, exist_ok=True)
        (self.e.claude / "settings.json").write_text("{}")
        foreign = [self.e.claude / "settings.json.bak-2020-01-01", self.e.claude / "settings.json.bak-refresh-20200101-0000"]
        for f in foreign:
            f.write_text("mine")
        for _ in range(5):
            self.e.install()
            time.sleep(1.05)
        ours = [p for p in self.e.claude.glob("settings.json.bak-*") if p not in foreign]
        self.assertEqual(len(ours), 3)
        for f in foreign:
            self.assertTrue(f.exists(), f"deleted a backup it did not create: {f.name}")

    def test_fresh_machine_without_transcripts_installs(self):
        os.rmdir(self.e.projects)
        os.rmdir(self.e.projects.parent)
        r = self.e.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_relative_archive_path_stored_absolute(self):
        env = dict(os.environ, HOME=str(self.e.home), CLAUDE_CONFIG_DIR=str(self.e.claude),
                   CLAUDE_SESSION_ARCHIVE_HOME=str(self.e.conf))
        subprocess.run([sys.executable, str(SCRIPT), "install", "--archive", "Archive"],
                       env=env, cwd=str(self.e.drive), capture_output=True, text=True)
        stored = json.loads((self.e.conf / "config.json").read_text())["archive"]
        self.assertTrue(Path(stored).is_absolute())

    def test_install_from_installed_copy(self):
        self.e.install()
        env = dict(os.environ, HOME=str(self.e.home), CLAUDE_CONFIG_DIR=str(self.e.claude),
                   CLAUDE_SESSION_ARCHIVE_HOME=str(self.e.conf))
        r = subprocess.run([sys.executable, str(self.e.conf / "claude_session_archive.py"), "install",
                            "--archive", str(self.e.drive / "Other")], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Other", json.loads((self.e.conf / "config.json").read_text())["archive"])

    def test_uninstall_removes_hook_and_files(self):
        self.e.install()
        self.e.run("uninstall")
        self.assertEqual(self.e.settings()["hooks"]["SessionStart"], [])
        self.assertFalse((self.e.conf / "claude_session_archive.py").exists())

    @unittest.skipUnless(POSIX, "POSIX shim")
    def test_shim_paths_are_shell_quoted(self):
        marker = Path(self.e.tmp.name) / "INJECTED"
        conf = Path(self.e.tmp.name) / f'c $(touch {marker}) "q"'
        r = self.e.run("install", "--archive", str(self.e.archive), extra_env={"CLAUDE_SESSION_ARCHIVE_HOME": str(conf)})
        self.assertEqual(r.returncode, 0, r.stderr)
        shim = self.e.home / ".local" / "bin" / "claude-session-archive"
        subprocess.run(["sh", str(shim), "--version"], capture_output=True,
                       env=dict(os.environ, CLAUDE_SESSION_ARCHIVE_HOME=str(conf)))
        self.assertFalse(marker.exists(), "command substitution in the path was executed")

    @unittest.skipUnless(POSIX, "POSIX shim")
    def test_foreign_shim_left_alone(self):
        shim = self.e.home / ".local" / "bin" / "claude-session-archive"
        shim.parent.mkdir(parents=True)
        shim.write_text("#!/bin/sh\necho someone else\n")
        self.e.install()
        self.e.run("uninstall")
        self.assertEqual(shim.read_text(), "#!/bin/sh\necho someone else\n")


class SyncTests(Base):
    def setUp(self):
        super().setUp()
        self.e.write_session("aaaa1111", transcript())
        r = self.e.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.copy = self.e.archive / "all-sessions" / "projects" / "-work-proj" / "aaaa1111.jsonl"

    def sync(self):
        return self.e.run("sync")

    def test_smaller_local_transcript_never_replaces_archive_copy(self):
        before = self.copy.read_text()
        self.e.write_session("aaaa1111", "{}\n")
        self.sync()
        self.assertEqual(self.copy.read_text(), before)
        kept = list((self.e.archive / "all-sessions" / "versions").rglob("aaaa1111.jsonl"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), "{}\n")

    def test_grown_transcript_replaces_archive_copy(self):
        grown = transcript(extra=line(type="user", timestamp="2026-10-02T00:00:00Z", message={"content": "more"}))
        self.e.write_session("aaaa1111", grown)
        self.sync()
        self.assertEqual(self.copy.read_text(), grown)

    def test_changed_memory_note_kept_beside_not_over(self):
        mem = self.e.projects / "memory"
        mem.mkdir()
        (mem / "note.md").write_text("version one")
        self.sync()
        (mem / "note.md").write_text("version two, longer")
        self.sync()
        arch = self.e.archive / "all-sessions" / "projects" / "-work-proj" / "memory" / "note.md"
        self.assertEqual(arch.read_text(), "version one")
        self.assertEqual(len(list((self.e.archive / "all-sessions" / "versions").rglob("note.md"))), 1)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_symlink_planted_in_archive_not_written_through(self):
        victim = self.e.home / "victim.txt"
        victim.write_text("original")
        target = self.e.archive / "all-sessions" / "projects" / "-work-proj" / "bbbb2222.jsonl"
        target.symlink_to(victim)
        self.e.write_session("bbbb2222", transcript())
        self.sync()
        self.assertEqual(victim.read_text(), "original")

    def test_title_cannot_inject_markup_and_index_is_stable(self):
        evil = "x\n# Injected heading <!-- sessions:end --> | cell"
        self.e.write_session("cccc3333", transcript(title=evil, prompt="look at <!-- sessions:end -->"))
        sizes = []
        for _ in range(3):
            self.sync()
            sizes.append((self.e.archive / "INDEX.md").stat().st_size)
        self.assertEqual(len(set(sizes)), 1, f"INDEX.md grew: {sizes}")
        for name in ("INDEX.md", "SESSIONS.md"):
            text = (self.e.archive / name).read_text(encoding="utf-8")
            self.assertNotIn("<!--", text)
            self.assertNotIn("\n# Injected", text)

    def test_curated_notes_survive_every_sync(self):
        cur = self.e.archive / "data" / "curated.md"
        cur.write_text("## My project\n\nhand written notes\n")
        self.sync()
        self.sync()
        self.assertEqual(cur.read_text(), "## My project\n\nhand written notes\n")
        self.assertIn("hand written notes", (self.e.archive / "INDEX.md").read_text())

    def test_groups_summaries_and_types(self):
        data = self.e.archive / "data"
        (data / "groups.json").write_text(json.dumps({"groups": {"Work": ["aaaa1111"]}}))
        (data / "summaries.json").write_text(json.dumps({"aaaa1111": {"summary": "Fixed it. More.", "keep": "k", "kind": "Bug fix"}}))
        self.sync()
        idx = (self.e.archive / "INDEX.md").read_text()
        self.assertIn("### Work (1)", idx)
        self.assertIn("| Bug fix |", idx)
        self.assertIn("By type: 1 bug fix.", idx)
        self.assertIn("**Keep:** k", (self.e.archive / "SESSIONS.md").read_text())

    def test_custom_title_prefix_groups_but_ai_title_colon_does_not(self):
        self.e.write_session("dddd4444", transcript(title="Marketing: launch"))
        self.e.write_session("eeee5555", transcript(title="Debugging: login", custom=False))
        self.sync()
        idx = (self.e.archive / "INDEX.md").read_text()
        self.assertIn("### Marketing (1)", idx)
        self.assertNotIn("### Debugging", idx)

    def test_bad_data_files_stop_rebuild_and_keep_previous_index(self):
        before = (self.e.archive / "SESSIONS.md").read_text()
        (self.e.archive / "data" / "summaries.json").write_text('{"x": 1,}')
        r = self.sync()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual((self.e.archive / "SESSIONS.md").read_text(), before)

    def test_wrong_typed_data_entries_ignored(self):
        data = self.e.archive / "data"
        (data / "groups.json").write_text(json.dumps({"groups": {"G": "a", "H": ["aaaa1111"]}}))
        (data / "summaries.json").write_text(json.dumps({"aaaa1111": "oops", "zz": {"summary": 5}}))
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("### H (1)", (self.e.archive / "INDEX.md").read_text())

    def test_if_stale_skips_and_running_lock_skips(self):
        self.assertEqual(self.e.run("sync", "--if-stale").stdout, "")
        (self.e.conf / "last-sync").unlink()
        (self.e.conf / "sync.lock").write_text("")
        self.assertIn("another sync is running", self.e.run("sync").stdout)

    def test_offline_archive_reported_not_crashed(self):
        (self.e.conf / "config.json").write_text(json.dumps({"archive": str(self.e.drive / "gone")}))
        r = self.sync()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not reachable", r.stdout)

    def test_where_shows_computer_app_and_remote_control_link(self):
        bridge = line(type="bridge-session", entrypoint="claude-desktop", bridgeSessionId="cse_01AbCdEf234567")
        self.e.write_session("ffff6666", transcript(title="Desk [work]", extra=bridge))
        self.sync()
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        row = next(l for l in idx.splitlines() if "ffff6666" in l)
        self.assertIn(f"{socket.gethostname()} · Desktop app, Remote Control", row)
        self.assertIn("[Desk \\[work\\]](https://claude.ai/code/session_01AbCdEf234567)", row)
        plain = next(l for l in idx.splitlines() if "aaaa1111" in l)
        self.assertNotIn("](", plain, "a session without Remote Control must not get a link")
        self.assertIn("On claude.ai: https://claude.ai/code/session_01AbCdEf234567",
                      (self.e.archive / "SESSIONS.md").read_text(encoding="utf-8"))

    def test_malformed_remote_control_id_not_linked(self):
        bad = line(type="bridge-session", bridgeSessionId="cse_x)](https://evil.example")
        self.e.write_session("abab7777", transcript(extra=bad))
        self.sync()
        self.assertNotIn("evil.example", (self.e.archive / "INDEX.md").read_text(encoding="utf-8"))

    def test_sessions_from_another_computer_name_it(self):
        projects = self.e.archive / "all-sessions" / "projects" / "-other-proj"
        projects.mkdir(parents=True)
        (projects / "bcbc8888.jsonl").write_text(transcript(title="Elsewhere"), encoding="utf-8")
        computers = self.e.archive / "all-sessions" / "computers"
        (computers / "other-pc.json").write_text(json.dumps(
            {"computer": "other-pc <b>", "sessions": ["-other-proj/bcbc8888.jsonl"]}))
        (computers / "broken.json").write_text("{not json")
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        row = next(l for l in idx.splitlines() if "bcbc8888" in l)
        self.assertIn("other-pc &lt;b&gt; · unknown app", row)  # the test transcript has no entrypoint
        own_name = re.sub(r"[^A-Za-z0-9._-]", "_", socket.gethostname())[:64] + ".json"
        own = json.loads((computers / own_name).read_text())
        self.assertIn("-work-proj/aaaa1111.jsonl", own["sessions"])
        self.assertNotIn("-other-proj/bcbc8888.jsonl", own["sessions"], "a computer lists only its own sessions")

    def test_cloud_sessions_listed_and_bad_links_dropped(self):
        (self.e.archive / "data" / "cloud.json").write_text(json.dumps({"sessions": [
            {"title": "Cloud idea", "url": "https://claude.ai/code/session_01GoodExample", "date": "2026-09-30",
             "group": "Ideas", "summary": "Explored it. More.", "kind": "Research", "repo": "me/repo",
             "branch": "feat/x <b>", "started_from": "Android app", "last": "2026-10-02",
             "first_request": "Look into <this>"},
            {"title": "Forged", "url": "https://claude.ai/x)[evil](https://evil.example"},
            {"title": "Script", "url": "javascript:alert(1)"},
            {"url": "https://claude.ai/code/session_01NoTitle"},
        ]}))
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("### Ideas (1)", idx)
        self.assertIn("| 2026-09-30 | Research | [Cloud idea](https://claude.ai/code/session_01GoodExample) "
                      "| Cloud · Android app · me/repo (feat/x &lt;b&gt;) | Explored it | `cloud` |", idx)
        forged = next(l for l in idx.splitlines() if "| Forged |" in l)
        self.assertIn("| Cloud |", forged, "an entry with no details still says Cloud")
        self.assertIn("## All sessions (4: 1 local, 3 cloud)", idx)
        self.assertNotIn("evil.example", idx)
        self.assertNotIn("javascript:", idx)
        self.assertIn("By type: 1 research.", idx)
        full = (self.e.archive / "SESSIONS.md").read_text(encoding="utf-8")
        self.assertIn("Transcript: not archived", full)
        saved = self.e.archive / "all-sessions" / "cloud"
        saved.mkdir(parents=True)
        (saved / "session_01GoodExample.json").write_text(json.dumps({"id": "session_01GoodExample", "events": []}))
        self.sync()
        full = (self.e.archive / "SESSIONS.md").read_text(encoding="utf-8")
        self.assertIn("Transcript: `all-sessions/cloud/session_01GoodExample.json` (a copy saved from claude.ai)", full)
        self.assertEqual(full.count("a copy saved from claude.ai"), 1, "only the entry whose id matches")
        self.assertIn("2026-09-30 to 2026-10-02 · repo · **Research**", full)
        self.assertIn("First request: _Look into &lt;this&gt;_", full)

    def test_last_sync_names_computer(self):
        self.assertIn(socket.gethostname(), (self.e.archive / "LAST-SYNC.txt").read_text())

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_created_files_private(self):
        self.sync()
        for p in (self.e.archive / "INDEX.md", self.e.archive / "SESSIONS.md", self.e.conf / "config.json"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode) & 0o077, 0, p)
        self.assertEqual(stat.S_IMODE((self.e.archive / "data").stat().st_mode) & 0o077, 0)

    def test_digest_and_cap(self):
        out = Path(self.e.tmp.name) / "digests"
        r = self.e.run("digest", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (out / "aaaa1111.txt").read_text()
        self.assertIn("USER: hello there", text)
        self.assertIn("ASSISTANT: done", text)
        self.assertEqual(self.e.run("digest", str(out), "--cap", "0").returncode, 1)

    def test_status_reports_runnable_hook(self):
        self.assertIn("installed, runnable", self.e.run("status").stdout)


def child_cpu_seconds(fn):
    """CPU time used by the subprocesses fn runs: unlike elapsed time, it does not depend on how
    busy the machine is, so a speed test measures the code, not the computer."""
    try:
        import resource
    except ImportError:  # Windows: fall back to elapsed time
        t = time.time()
        fn()
        return time.time() - t
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    fn()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)


def fake(prefix, n, alphabet="A1b2C3d4E5f6G7h8J9k0"):
    """A credential-shaped value built at run time, so this file holds nothing that looks real."""
    return prefix + (alphabet * 20)[:n]


def export_line(sid, kind="bridge", title="Recovered work", created="2026-04-01T09:00:00Z", text="please fix the login", origin="claude_code_cli", repo="me/app"):
    meta = {"title": title, "created_at": created, "last_event_at": "2026-04-02T09:00:00Z", "environment_kind": kind,
            "config": {"origin": origin}, "external_metadata": {"current_branches": {repo: "main"}}}
    events = [{"sequence_num": "1", "event_type": "user", "payload": {"message": {"role": "user", "content": text}}},
              {"sequence_num": "2", "event_type": "assistant",
               "payload": {"message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed it."}]}}}]
    return json.dumps({"id": sid, "listing": {"id": "cse_" + sid[8:]}, "metadata": meta, "events": events}) + "\n"


class ImportTests(Base):
    def setUp(self):
        super().setUp()
        self.e.write_session("aaaa1111", transcript())
        self.assertEqual(self.e.install().returncode, 0)
        self.saved = self.e.archive / "all-sessions" / "cloud"

    def test_import_gzip_jsonl_recovers_lost_sessions_into_the_index(self):
        export = Path(self.e.tmp.name) / "export.jsonl.gz"
        with gzip.open(export, "wt", encoding="utf-8") as fh:
            fh.write(export_line("session_01RecoveredOne1"))
            fh.write(export_line("session_01CloudOne22222", kind="anthropic_cloud", title="Phone idea", origin="android"))
            fh.write(json.dumps({"id": "../../etc/passwd", "events": []}) + "\n")
            fh.write(json.dumps({"id": "session_01NoEvents333"}) + "\n")
        r = self.e.run("import", str(export))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 new, 0 updated, 0 unchanged, 0 kept beside, 2 skipped", r.stdout)
        self.assertIn("  + Recovered work", r.stdout)
        self.assertIn("delete them", r.stdout)
        self.assertEqual(sorted(p.name for p in self.saved.iterdir()),
                         ["session_01CloudOne22222.json", "session_01RecoveredOne1.json"])
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("(3: 1 local, 1 recovered, 1 cloud)", idx)
        row = next(l for l in idx.splitlines() if "Recovered work" in l)
        self.assertIn("[Recovered work](https://claude.ai/code/session_01RecoveredOne1)", row)
        self.assertIn("Recovered from claude.ai · Terminal, Remote Control · me/app", row)
        self.assertIn("Cloud · Android app", next(l for l in idx.splitlines() if "Phone idea" in l))
        full = (self.e.archive / "SESSIONS.md").read_text(encoding="utf-8")
        self.assertIn("Transcript: `all-sessions/cloud/session_01RecoveredOne1.json` (a copy saved from claude.ai)", full)
        out = Path(self.e.tmp.name) / "digests"
        self.e.run("digest", str(out))
        self.assertIn("USER: please fix the login", (out / "session_01RecoveredOne1.txt").read_text())

    def test_import_never_replaces_a_fuller_copy(self):
        big = Path(self.e.tmp.name) / "big.jsonl"
        big.write_text(export_line("session_01Keep00000000"))
        self.e.run("import", str(big))
        small = json.loads(export_line("session_01Keep00000000"))
        small["events"] = small["events"][:1]
        (Path(self.e.tmp.name) / "small.jsonl").write_text(json.dumps(small) + "\n")
        r = self.e.run("import", str(Path(self.e.tmp.name) / "small.jsonl"))
        self.assertIn("0 new, 0 updated, 1 unchanged", r.stdout)
        self.assertEqual(len(json.loads((self.saved / "session_01Keep00000000.json").read_text())["events"]), 2)

    def test_local_remote_control_session_is_not_listed_twice(self):
        bridge = line(type="bridge-session", bridgeSessionId="cse_01Twice0000000")
        self.e.write_session("bbbb2222", transcript(title="Here locally", extra=bridge))
        self.e.run("sync")
        f = Path(self.e.tmp.name) / "e.jsonl"
        f.write_text(export_line("session_01Twice0000000", title="Here locally"))
        self.e.run("import", str(f))
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(idx.count("Here locally"), 1)
        self.assertIn("Also saved from claude.ai: `all-sessions/cloud/session_01Twice0000000.json`",
                      (self.e.archive / "SESSIONS.md").read_text(encoding="utf-8"))


class ReviewTests(Base):
    """One test per finding of the 1.1 review (securityaudit.md, F1 to F12)."""

    def setUp(self):
        super().setUp()
        self.e.write_session("aaaa1111", transcript())
        self.assertEqual(self.e.install().returncode, 0)
        self.saved = self.e.archive / "all-sessions" / "cloud"
        self.tmp = Path(self.e.tmp.name)

    def imp(self, *records, name="e.jsonl", extra_env=None):
        f = self.tmp / name
        f.write_text("".join(r if isinstance(r, str) else json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return self.e.run("import", str(f), extra_env=extra_env)

    def test_f1_no_links_images_or_html_from_imported_text(self):
        rec = json.loads(export_line("session_01Hostile0000", title="[click](https://evil.example) ![i](https://evil.example/p.png) `code",
                                     created="<img src=x onerror=alert(1)>", repo="[r](https://evil.example)",
                                     text="see [here](https://evil.example)"))
        self.imp(rec)
        for name in ("INDEX.md", "SESSIONS.md"):
            text = (self.e.archive / name).read_text(encoding="utf-8")
            self.assertNotIn("https://evil", text, name)  # not even as a bare, auto-linked URL
            self.assertNotIn("<img", text, name)
            self.assertIn("\\`code", text, name)  # a backtick cannot open a code span across cells

    def test_f2_malformed_saved_record_does_not_stop_the_index(self):
        self.saved.mkdir(parents=True)
        bad = json.loads(export_line("session_01BadShape000"))
        bad["metadata"]["config"]["outcomes"] = 1
        (self.saved / "session_01BadShape000.json").write_text(json.dumps(bad))
        (self.saved / "session_01DeepNest0000.json").write_text('{"events": [' + "[" * 100000 + "]" * 100000 + "]}")
        self.imp(export_line("session_01GoodOne00000", title="Still listed"))
        r = self.e.run("sync")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("Still listed", (self.e.archive / "INDEX.md").read_text(encoding="utf-8"))

    def test_f3_a_different_copy_is_kept_beside_never_over(self):
        self.imp(export_line("session_01Keep11111111"))
        junk = json.loads(export_line("session_01Keep11111111"))
        junk["events"] = [{"sequence_num": str(i), "event_type": "system", "payload": {}} for i in range(10, 20)]
        r = self.imp(junk)
        self.assertIn("1 kept beside", r.stdout)
        kept = json.loads((self.saved / "session_01Keep11111111.json").read_text())
        self.assertEqual(len(kept["events"]), 2, "the original stays in place")
        self.assertEqual(len(list((self.saved / "versions").glob("session_01Keep11111111.*.json"))), 1)
        (self.saved / "session_01Unread000000.json").write_text("{not json")
        self.imp(export_line("session_01Unread000000"))
        self.assertEqual((self.saved / "session_01Unread000000.json").read_text(), "{not json")

    def test_f3_a_grown_copy_replaces(self):
        self.imp(export_line("session_01Grow00000000"))
        grown = json.loads(export_line("session_01Grow00000000"))
        grown["events"].append({"sequence_num": "3", "event_type": "user", "payload": {"message": {"content": "more"}}})
        self.assertIn("1 updated", self.imp(grown).stdout)

    def test_f8_bad_files_report_a_problem_not_a_traceback(self):
        good = export_line("session_01Fine00000000")
        gz = self.tmp / "trunc.jsonl.gz"
        with gzip.open(gz, "wt", encoding="utf-8") as fh:
            fh.write(good * 50)
        gz.write_bytes(gz.read_bytes()[:-30])
        bom = self.tmp / "bom.jsonl"
        bom.write_bytes(b"\xef\xbb\xbf" + export_line("session_01Bom000000000").encode())
        pretty = self.tmp / "pretty.json"
        pretty.write_text(json.dumps({"sessions": [json.loads(export_line("session_01Pretty000000"))]}, indent=2))
        latin = self.tmp / "latin.jsonl"
        latin.write_bytes(b"\xff\xfe not utf-8\n")
        r = self.e.run("import", str(gz), str(bom), str(pretty), str(latin), str(self.tmp))
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1)
        self.assertIn("problem:", r.stdout)
        for sid in ("session_01Fine00000000", "session_01Bom000000000", "session_01Pretty000000"):
            self.assertTrue((self.saved / f"{sid}.json").exists(), sid)
        self.assertIn("sessions indexed", r.stdout)

    def test_f6_records_without_export_structure_are_skipped(self):
        r = self.imp({"id": "session_01Bare00000000", "events": []})
        self.assertIn("1 skipped", r.stdout)
        self.assertFalse((self.saved / "session_01Bare00000000.json").exists())

    def test_f9_oversized_record_is_refused(self):
        big = json.loads(export_line("session_01Huge00000000"))
        big["events"][0]["payload"]["message"]["content"] = "x" * 50000
        r = self.imp(big, export_line("session_01Small0000000"), extra_env={"CLAUDE_SESSION_ARCHIVE_MAX_RECORD_BYTES": "20000"})
        self.assertIn("size limit", r.stdout)
        self.assertFalse((self.saved / "session_01Huge00000000.json").exists())
        self.assertTrue((self.saved / "session_01Small0000000.json").exists())

    @unittest.skipUnless(POSIX, "symlinks")
    def test_f10_symlinked_cloud_folder_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.e.archive / "all-sessions" / "cloud").symlink_to(outside)
        r = self.imp(export_line("session_01Escape000000"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_f11_uninstall_removes_every_cache(self):
        self.imp(export_line("session_01Cache0000000"))
        self.e.run("secrets")
        self.assertTrue((self.e.conf / "saved-cache.json").exists())
        self.e.run("uninstall")
        for name in ("saved-cache.json", "secrets-cache.json", "index-cache.json"):
            self.assertFalse((self.e.conf / name).exists(), name)

    def test_cloud_url_with_query_is_listed_once(self):
        self.imp(export_line("session_01Query0000000", kind="anthropic_cloud", title="Asked once"))
        (self.e.archive / "data" / "cloud.json").write_text(json.dumps({"sessions": [
            {"title": "Asked once", "url": "https://claude.ai/code/session_01Query0000000?tab=chat"}]}))
        self.e.run("index")
        self.assertEqual((self.e.archive / "INDEX.md").read_text(encoding="utf-8").count("Asked once"), 1)


class SecretsReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.aws = "AKIA" + "Q7RT2MZP4KD9WX3N"
        self.key_a = "-----BEGIN RSA PRIVATE KEY-----\n" + fake("MIIE", 120)
        self.key_b = "-----BEGIN RSA PRIVATE KEY-----\n" + fake("MIIE", 120, alphabet="Zy9Xw8Vu7Ts6Rq5Po4Nm")
        prompt = ("first line\n" + self.aws + "\tand DB_PASSWORD=Xk29mPq7Lw4n and\npassword: S3cretPass99\n"
                  "password: process.env.DB_PASSWORD and token: string\n" + self.key_a + "\n" + self.key_b)
        self.e.write_session("aaaa1111", transcript(prompt=prompt))
        self.assertEqual(self.e.install().returncode, 0)
        self.e.run("secrets")
        self.report = (self.e.archive / "SECRETS.md").read_text(encoding="utf-8")

    def test_f4_keys_after_json_escapes_and_env_assignments_found(self):
        self.assertIn("| AWS access key id |", self.report)
        self.assertEqual(self.report.count("| Password or secret assignment |"), 2, self.report)
        self.assertNotIn("proc", self.report)

    def test_google_key_in_a_url_query_is_found(self):
        self.e.write_session("cccc3333", transcript(prompt="GET https://maps.example/v1/x?key=" + fake("AIza", 35) + "&q=1"))
        self.e.run("sync")
        self.assertIn("| Google API key |", (self.e.archive / "SECRETS.md").read_text(encoding="utf-8"))

    def test_assignment_noise_is_not_reported(self):
        self.e.write_session("dddd4444", transcript(prompt=(
            "from_secret: gh_token2 and BETTER_AUTH_SECRET=replace-with-random-string and "
            "STRIPE_SECRET_KEY= NEXT_VAR9=1 and SECRET='abcdefghijk'")))
        self.e.run("sync")
        self.assertEqual((self.e.archive / "SECRETS.md").read_text(encoding="utf-8").count("| Password or secret assignment |"), 2,
                         "only the two real assignments from setUp")

    def test_f5_each_private_key_has_its_own_fingerprint(self):
        self.assertEqual(self.report.count("| Private key |"), 2)

    def test_f7_fingerprints_are_keyed_and_short_values_barely_shown(self):
        import hashlib
        salt = (self.e.archive / "data" / ".secrets-salt").read_text().strip()
        self.assertRegex(salt, r"^[0-9a-f]{64}$")
        self.assertNotIn(hashlib.sha256(self.aws.encode()).hexdigest()[:12], self.report)
        self.assertIn("`(12 chars)`", self.report)
        self.assertNotIn("Xk", self.report, "a password shows its length only")

    def test_f12_many_matches_on_one_line_stay_fast(self):
        unit = "-eyJAAAAAAAAAA.eyJAAAAAAAAAA"  # the reviewer's input: thousands of JWT-shaped tokens
        self.e.write_session("bbbb2222", transcript(prompt=unit * (3000000 // len(unit))))
        used = child_cpu_seconds(lambda: self.e.run("sync"))  # the sync scans the new file (not from cache)
        self.assertLess(used, 5)


class SecretsTests(Base):
    def setUp(self):
        super().setUp()
        self.keys = {
            "Anthropic API key": fake("sk-ant-api03-", 90),
            "AWS access key id": "AKIA" + "Q7RT2MZP4KD9WX3N",
            "GitHub token": fake("ghp_", 36),
            "Database URL with password": "postgres://test_runner:" + fake("pw", 14) + "@db.internal:5432/app",
        }
        pasted = " and ".join(self.keys.values())
        self.e.write_session("aaaa1111", transcript(prompt="use these: " + pasted + ' and password="' + fake("Pq", 12) + '"'))
        self.e.write_session("cccc3333", transcript(prompt="again " + self.keys["GitHub token"]))
        self.e.write_session("dddd4444", transcript(prompt='api_key="your-api-key-here" and AKIAXXXXXXXXXXXXXXXX'))
        self.assertEqual(self.e.install().returncode, 0)

    def test_finds_each_kind_and_never_writes_the_value(self):
        r = self.e.run("secrets")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        report = (self.e.archive / "SECRETS.md").read_text(encoding="utf-8")
        for kind in list(self.keys) + ["Password or secret assignment"]:
            self.assertIn(f"| {kind} |", report)
        for value in self.keys.values():
            self.assertNotIn(value, report)
            self.assertNotIn(value, r.stdout + r.stderr)
        gh = next(l for l in report.splitlines() if "| GitHub token |" in l)
        self.assertIn("| 2 | 2 |", gh, "the same token in two sessions is one value seen twice")
        # placeholders are not reported: one real AWS key, one real password assignment
        self.assertEqual(report.count("| AWS access key id |"), 1)
        self.assertEqual(report.count("| Password or secret assignment |"), 1)
        self.assertIn("**5 distinct value(s)** found", report)

    def test_hyphenated_words_are_not_keys(self):
        self.e.write_session("ffff6666", transcript(prompt="see risk-assessment-for-third-party-vendor-onboarding-checklist "
                                                           "and task-scheduler-configuration-for-nightly-reports-runs"))
        self.e.run("sync")
        self.e.run("secrets")
        self.assertNotIn("OpenAI API key", (self.e.archive / "SECRETS.md").read_text(encoding="utf-8"))

    def test_key_shapes_inside_base64_are_not_keys(self):
        blob = "iVBORw0KGgoAAAANSUhEUgAA/CgANAB+FAB26UAGaAD8KAKuqf8eMv0oAYPp+" + fake("AIza", 35) + "+N8aE1dM2HvQAGgAo=="
        self.e.write_session("ffff7777", transcript(prompt="data:image/png;base64," + blob))
        self.e.run("sync")
        self.e.run("secrets")
        self.assertNotIn("Google API key", (self.e.archive / "SECRETS.md").read_text(encoding="utf-8"))

    def test_ignore_list_and_fail_flag(self):
        report = (lambda: (self.e.archive / "SECRETS.md").read_text(encoding="utf-8"))
        self.e.run("secrets")
        fp = re.search(r"\| GitHub token \|.*`([0-9a-f]{12})` \|$", report(), re.M).group(1)
        (self.e.archive / "data" / "secrets-ignore.json").write_text(json.dumps({"ignore": [fp]}))
        r = self.e.run("secrets", "--fail")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("| GitHub token |", report())

    def test_sync_scans_and_index_reports_it(self):
        r = self.e.run("sync")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.e.archive / "SECRETS.md").exists(), "sync writes the report")
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("**Secrets:** 5 credential-shaped value(s)", idx)

    def test_cache_holds_no_value_and_skips_unchanged_files(self):
        self.e.run("secrets")
        cache = (self.e.conf / "secrets-cache.json").read_text(encoding="utf-8")
        for value in self.keys.values():
            self.assertNotIn(value, cache)
        data = json.loads(cache)
        # poison the cache entry of one file: if it were re-read, the hit would come back
        rel = next(k for k, v in data["files"].items() if v["hits"] and k.endswith("cccc3333.jsonl"))
        data["files"][rel]["hits"] = []
        (self.e.conf / "secrets-cache.json").write_text(json.dumps(data))
        self.e.run("secrets")
        gh = next(l for l in (self.e.archive / "SECRETS.md").read_text().splitlines() if "| GitHub token |" in l)
        self.assertIn("| 1 | 1 |", gh, "an unchanged file is served from the cache, not re-read")
        # a grown file is re-read
        p = self.e.archive / "all-sessions" / "projects" / "-work-proj" / "cccc3333.jsonl"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line(type="user", timestamp="2026-10-02T10:00:00Z", message={"role": "user", "content": "more"}))
        self.e.run("secrets")
        gh = next(l for l in (self.e.archive / "SECRETS.md").read_text().splitlines() if "| GitHub token |" in l)
        self.assertIn("| 2 | 2 |", gh)

    def test_long_lines_stay_fast(self):
        self.e.write_session("eeee5555", transcript(prompt="eyJ" + "a" * 400000 + " sk-" + "-" * 300000 + " AKIA" + "Z" * 200000))
        self.e.run("sync")
        self.assertLess(child_cpu_seconds(lambda: self.assertEqual(self.e.run("secrets").returncode, 0)), 20)


class TriageAndRedactTests(Base):
    def setUp(self):
        super().setUp()
        self.real = "postgres://app:" + "Pq7x9Lm2Kd4wZ8" + "@db.prod.example-corp.net:5432/main"
        self.word = "Pq7x9Lm2Kd4wZ8"
        self.gh = fake("ghp_", 36)
        self.e.write_session("aaaa1111", transcript(prompt="connect with " + self.real + " please. Also the word "
                                                    + self.word + " on its own, and in test fixture: " + self.gh))
        self.e.write_session("bbbb2222", transcript(prompt="local dev postgres://postgres:Loc4lPass99@localhost:5433/postgres"))
        old = time.time() - 3600
        for p in self.e.projects.glob("*.jsonl"):
            os.utime(p, (old, old))
        self.assertEqual(self.e.install().returncode, 0)
        self.e.run("secrets")

    def report(self):
        return (self.e.archive / "SECRETS.md").read_text(encoding="utf-8")

    def fp(self, kind):
        return re.search(r"^\|.*\| " + re.escape(kind) + r" \|.*`([0-9a-f]{12})` \|$", self.report(), re.M).group(1)

    def test_attention_first_others_folded_with_links(self):
        r = self.report()
        attention, folded = r.split("<details>")
        self.assertIn("app&#64;db.prod.example-corp.net", attention)  # "@" as an entity: never an e-mail link
        self.assertIn("⚠️", attention)
        self.assertNotIn("postgres&#64;localhost", attention)
        self.assertIn("local or example database host", folded)
        self.assertIn("| ⚠️ | GitHub token |", attention, "a live-format key is never folded for nearby wording")
        self.assertIn("](all-sessions/projects/-work-proj/aaaa1111.jsonl)", attention)
        self.assertIn("**2 need attention**", r)
        self.assertIn("⚠️ 2 need attention", (self.e.archive / "INDEX.md").read_text(encoding="utf-8"))

    def test_triage_file_moves_a_row(self):
        fp = self.fp("Database URL with password")
        (self.e.archive / "data" / "secrets-triage.json").write_text(json.dumps({fp: {"status": "rotated", "note": "done 10-12"}}))
        self.e.run("secrets")
        attention, folded = self.report().split("<details>")
        self.assertNotIn(fp, attention)
        self.assertIn("rotated: done 10-12", folded)

    def test_redact_dry_run_changes_nothing(self):
        before = (self.e.projects / "aaaa1111.jsonl").read_bytes()
        r = self.e.run("redact", self.fp("Database URL with password"))
        self.assertIn("would replace 2 occurrence(s)", r.stdout)  # archive copy and local transcript
        self.assertEqual((self.e.projects / "aaaa1111.jsonl").read_bytes(), before)

    def test_redact_apply_replaces_only_the_value_everywhere_and_sync_keeps_it_out(self):
        fp = self.fp("Database URL with password")
        r = self.e.run("redact", fp, "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for p in (self.e.projects / "aaaa1111.jsonl", self.e.archive / "all-sessions/projects/-work-proj/aaaa1111.jsonl"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("postgres://app:" + self.word, text)
            self.assertIn("<secret removed: Database URL with password, app@db.prod.example-corp.net, " + fp + ">", text)
            self.assertIn(" " + self.word + " on its own", text, "only the matched value is replaced")
            for line in text.splitlines():
                json.loads(line)
        self.e.run("sync")
        self.assertFalse((self.e.archive / "all-sessions" / "versions").exists(), "nothing brought back beside it")
        self.assertNotIn(fp, self.report())

    def test_redact_skips_a_transcript_in_use(self):
        os.utime(self.e.projects / "aaaa1111.jsonl", None)  # just written: a running session
        r = self.e.run("redact", self.fp("Database URL with password"), "--apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("skipped, in use", r.stdout)
        self.assertIn("postgres://app:" + self.word, (self.e.projects / "aaaa1111.jsonl").read_text())

    def test_redact_refuses_something_that_is_not_a_fingerprint(self):
        r = self.e.run("redact", "../../etc", "--apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a fingerprint", r.stdout + r.stderr)


class RedactReviewTests(Base):
    """One test per finding of the review of redact (securityaudit.md, R1 to R10)."""

    def setUp(self):
        super().setUp()
        self.old = time.time() - 3600

    def put(self, sid, prompt):
        self.e.write_session(sid, transcript(prompt=prompt))
        os.utime(self.e.projects / f"{sid}.jsonl", (self.old, self.old))

    def ready(self):
        self.assertEqual(self.e.install().returncode, 0)
        self.e.run("secrets")
        return (self.e.archive / "SECRETS.md").read_text(encoding="utf-8")

    def fp(self, report, kind):
        return re.search(r"^\|.*\| " + re.escape(kind) + r" \|.*`([0-9a-f]{12})` \|$", report, re.M).group(1)

    def archive_copy(self, sid):
        return self.e.archive / "all-sessions" / "projects" / "-work-proj" / f"{sid}.jsonl"

    def test_r1_whole_private_key_is_redacted(self):
        body = [fake("MII", 64, alphabet="Ab3Cd5Ef7Gh9Jk2Lm4Np")] + [fake("", 64, alphabet=a) for a in
                ("Qr6St8Uv0Wx1Yz3Ab5Cd", "Ef7Gh9Jk2Lm4Np6Qr8St", "Uv0Wx1Yz3Ab5Cd7Ef9Gh")]
        key = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END RSA PRIVATE KEY-----"
        self.put("aaaa1111", "my key:\n" + key)
        fp = self.fp(self.ready(), "Private key")
        self.assertEqual(self.e.run("redact", fp, "--apply").returncode, 0)
        for p in (self.e.projects / "aaaa1111.jsonl", self.archive_copy("aaaa1111")):
            text = p.read_text(encoding="utf-8")
            for line in body:
                self.assertNotIn(line, text, "no line of the key survives")
        self.assertNotIn(fp, (self.e.archive / "SECRETS.md").read_text(encoding="utf-8"))

    def test_r2_value_with_punctuation_is_replaced_whole_and_other_forms_reported(self):
        self.put("aaaa1111", "DB_PASSWORD=Hunter2024;Zq9!x8Lm and later: mysql -pHunter2024;Zq9!x8Lm db")
        fp = self.fp(self.ready(), "Password or secret assignment")
        r = self.e.run("redact", fp, "--apply")
        text = (self.e.projects / "aaaa1111.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("DB_PASSWORD=Hunter2024", text)
        self.assertNotIn("DB_PASSWORD=<secret removed: Password or secret assignment, DB_PASSWORD, " + fp + ">;Zq9", text)
        self.assertIn("the same text still appears", r.stdout)
        self.assertNotIn("Hunter2024", r.stdout + r.stderr, "the report never prints the value")

    def test_encrypted_and_code_values_are_not_secrets(self):
        self.put("aaaa1111", "sops: WOODPECKER_AGENT_SECRET: ENC[AES256_GCM,data:Zm9vYmFy9x,iv:abc123==,tag:x9==,type:str] and "
                             "minified password},{name:!0,type:String} and DB_PASSWORD=Hunter2024;Zq9x8Lm; done and "
                             'quoted secret: "ENC[AES256_GCM,data:Zm9vYmFy9x,iv:abc123==,tag:x9==,type:str]"')
        attention = self.ready().split("<details>")[0]
        self.assertEqual(attention.count("| ⚠️ |"), 1, attention)  # only DB_PASSWORD
        self.assertIn("`(18 chars)`", attention, "the trailing ; is not part of the value")

    def test_leftover_check_ignores_an_ordinary_word(self):
        self.put("aaaa1111", "local postgres://postgres:postgres@db.prod.example-corp.net:5432/postgres uses postgres a lot")
        fp = self.fp(self.ready(), "Database URL with password")
        r = self.e.run("redact", fp, "--apply")
        self.assertNotIn("still appears", r.stdout)

    def test_r3_in_use_transcript_skips_its_archive_copy_too(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        fp = self.fp(self.ready(), "Database URL with password")
        os.utime(self.e.projects / "aaaa1111.jsonl", None)  # running session
        r = self.e.run("redact", fp, "--apply")
        self.assertIn("with its archive copy", r.stdout)
        self.assertIn("Pq7x9Lm2Kd4wZ8", self.archive_copy("aaaa1111").read_text(encoding="utf-8"),
                      "the archive copy is not redacted alone")

    def test_r3_sync_reapplies_redactions_to_whatever_it_copies(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        fp = self.fp(self.ready(), "Database URL with password")
        self.e.run("redact", fp, "--apply")
        # another computer's unredacted copy of a new session arrives via this computer's projects
        self.e.write_session("cccc3333", transcript(prompt="again postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main"))
        self.e.run("sync")
        self.assertNotIn("Pq7x9Lm2Kd4wZ8", self.archive_copy("cccc3333").read_text(encoding="utf-8"))
        self.assertTrue((self.e.archive / "data" / "redacted.json").exists())

    def test_r4_redact_keeps_timestamps(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        fp = self.fp(self.ready(), "Database URL with password")
        self.e.run("redact", fp, "--apply")
        self.assertAlmostEqual((self.e.projects / "aaaa1111.jsonl").stat().st_mtime, self.old, delta=2)

    def test_r5_import_reapplies_redactions(self):
        self.assertEqual(self.e.install().returncode, 0)
        rec = json.loads(export_line("session_01Redact000000", text="use postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net/x"))
        f = Path(self.e.tmp.name) / "a.jsonl"
        f.write_text(json.dumps(rec) + "\n")
        self.e.run("import", str(f))
        self.e.run("secrets")
        fp = self.fp((self.e.archive / "SECRETS.md").read_text(encoding="utf-8"), "Database URL with password")
        self.e.run("redact", fp, "--apply")
        rec["events"].append({"sequence_num": "3", "event_type": "user", "payload": {"message": {"content": "more"}}})
        f.write_text(json.dumps(rec) + "\n")
        self.assertIn("1 updated", self.e.run("import", str(f)).stdout)
        self.assertNotIn("Pq7x9Lm2Kd4wZ8", (self.e.archive / "all-sessions" / "cloud" / "session_01Redact000000.json").read_text())

    def test_r6_latest_or_internal_host_does_not_fold_a_real_credential(self):
        self.put("aaaa1111", "on the latest build use postgres://app:Pq7x9Lm2Kd4wZ8@db.internal:5432/main")
        attention = self.ready().split("<details>")[0]
        self.assertIn("Database URL with password", attention)

    def test_r7_unknown_fingerprint_is_reported(self):
        self.put("aaaa1111", "nothing here")
        self.ready()
        r = self.e.run("redact", "0123456789ab", "--apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stdout)

    def test_r9_line_endings_are_preserved(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        p = self.e.projects / "aaaa1111.jsonl"
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        os.utime(p, (self.old, self.old))
        fp = self.fp(self.ready(), "Database URL with password")
        self.e.run("redact", fp, "--apply")
        data = p.read_bytes()
        self.assertEqual(data.count(b"\r\n"), 3)
        self.assertNotIn(b"Pq7x9Lm2Kd4wZ8", data)

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "POSIX permissions")
    def test_r8_a_write_failure_is_reported_and_the_rest_continues(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        fp = self.fp(self.ready(), "Database URL with password")
        folder = self.archive_copy("aaaa1111").parent
        os.chmod(folder, 0o500)  # the archive copy cannot be rewritten
        try:
            r = self.e.run("redact", fp, "--apply")
        finally:
            os.chmod(folder, 0o700)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("could not write", r.stdout)
        self.assertNotIn("Pq7x9Lm2Kd4wZ8", (self.e.projects / "aaaa1111.jsonl").read_text(encoding="utf-8"))

    def test_s3_missing_salt_is_never_silently_replaced(self):
        self.put("aaaa1111", "postgres://app:Pq7x9Lm2Kd4wZ8@db.prod.example-corp.net:5432/main")
        self.ready()
        (self.e.archive / "data" / ".secrets-salt").unlink()
        r = self.e.run("secrets")
        self.assertEqual(r.returncode, 1)
        self.assertIn("finish syncing", r.stdout + r.stderr)
        self.assertFalse((self.e.archive / "data" / ".secrets-salt").exists())
        self.assertEqual(self.e.run("sync").returncode, 0, "a sync still keeps the archive")

    def test_r10_session_of_layouts(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("csa", SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual(m.session_of("all-sessions/versions/host/-p/abc.jsonl"), "abc")
        self.assertEqual(m.session_of("all-sessions/projects/-p/abc/subagents/x.jsonl"), "abc")
        self.assertEqual(m.session_of("all-sessions/projects/-p/memory/MEMORY.md"), "")
        self.assertEqual(m.session_of("all-sessions/cloud/versions/session_01abcdefgh.1234.json"), "session_01abcdefgh")


class SettingsAndReminderTests(Base):
    def setUp(self):
        super().setUp()
        self.e.write_session("aaaa1111", transcript())
        self.assertEqual(self.e.install().returncode, 0)
        self.settings = self.e.archive / "data" / "settings.json"

    def remind(self):
        (self.e.conf / "last-reminder").unlink() if (self.e.conf / "last-reminder").exists() else None
        r = self.e.run("remind")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else ""

    def imported(self, days_ago):
        when = datetime.now() - timedelta(days=days_ago)
        (self.e.archive / "data" / "claude-ai-import.json").write_text(json.dumps({"last": when.isoformat(timespec="seconds"), "computer": "pc"}))

    def test_install_writes_default_settings_and_both_hooks(self):
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["remind_claude_ai_import_every_days"], 14)
        hooks = [h for e in self.e.settings()["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual([h["args"][1:] for h in hooks], [["sync", "--if-stale"], ["remind"]])
        self.assertTrue(hooks[0]["async"])
        self.assertNotIn("async", hooks[1], "the reminder must run synchronously to be shown")
        self.e.run("uninstall")
        self.assertEqual(self.e.settings()["hooks"]["SessionStart"], [])

    def test_settings_are_never_overwritten(self):
        self.settings.write_text(json.dumps({"remind_claude_ai_import_every_days": 30}))
        self.e.install()
        self.assertEqual(json.loads(self.settings.read_text()), {"remind_claude_ai_import_every_days": 30})

    def test_reminds_when_never_or_overdue_and_not_when_recent(self):
        self.assertIn("never been saved", self.remind())
        self.imported(20)
        self.assertIn("20 days ago", self.remind())
        self.imported(2)
        self.assertEqual(self.remind(), "")
        self.settings.write_text(json.dumps({"remind_claude_ai_import_every_days": 1}))
        self.assertIn("2 days ago", self.remind(), "the shared setting is honoured")

    def test_at_most_once_a_day(self):
        self.assertTrue(self.e.run("remind").stdout.strip())
        self.assertEqual(self.e.run("remind").stdout.strip(), "")

    def test_reminders_off_and_bad_settings_fall_back_safely(self):
        self.settings.write_text(json.dumps({"reminders": False}))
        self.assertEqual(self.remind(), "")
        self.settings.write_text("{not json")
        self.assertEqual(self.remind(), "", "an unreadable file (drive mid-sync) means the last good settings")
        (self.e.conf / "settings-cache.json").unlink()
        self.assertIn("never been saved", self.remind(), "and with none seen yet, the defaults")
        self.settings.write_text(json.dumps({"remind_claude_ai_import_every_days": -5, "reminders": "yes"}))
        self.assertIn("reminder every 14 days", self.remind())

    def test_reminds_when_this_computer_stopped_syncing(self):
        self.imported(1)
        old = time.time() - 5 * 86400
        os.utime(self.e.conf / "last-ok", (old, old))
        self.assertIn("last synced 5 days ago", self.remind())

    def test_remind_never_fails(self):
        (self.e.conf / "config.json").write_text("{broken")
        r = self.e.run("remind")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "POSIX permissions")
    def test_remind_survives_an_unwritable_config_folder(self):
        os.chmod(self.e.conf, 0o500)  # recording "reminded today" will fail
        try:
            r = self.e.run("remind")
        finally:
            os.chmod(self.e.conf, 0o700)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_import_records_the_check_in_and_index_shows_it(self):
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("**claude.ai sessions:** never saved", idx)
        f = Path(self.e.tmp.name) / "e.jsonl"
        f.write_text(export_line("session_01Checkin00000"))
        self.e.run("import", str(f))
        self.assertIn("last", json.loads((self.e.archive / "data" / "claude-ai-import.json").read_text()))
        self.assertEqual(self.remind(), "")
        idx = (self.e.archive / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("**claude.ai sessions:** last saved", idx)
        self.imported(40)
        self.e.run("index")
        self.assertIn("⚠️ overdue", (self.e.archive / "INDEX.md").read_text(encoding="utf-8"))

    def test_sync_interval_and_secrets_switch_from_settings(self):
        self.settings.write_text(json.dumps({"scan_secrets_on_sync": False}))
        if (self.e.archive / "SECRETS.md").exists():
            (self.e.archive / "SECRETS.md").unlink()
        self.e.run("sync")
        self.assertFalse((self.e.archive / "SECRETS.md").exists())
        self.settings.write_text(json.dumps({"sync_every_hours": 1}))
        self.e.run("sync")  # remembers the setting locally
        old = time.time() - 2 * 3600
        os.utime(self.e.conf / "last-sync", (old, old))
        before = (self.e.conf / "last-sync").stat().st_mtime
        self.e.run("sync", "--if-stale")
        self.assertGreater((self.e.conf / "last-sync").stat().st_mtime, before, "2 h > 1 h setting: it synced")


class SourceTests(unittest.TestCase):
    def test_python_38_syntax(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_no_network_or_subprocess_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                 for a in (n.names if isinstance(n, ast.Import) else [type("m", (), {"name": n.module or ""})])}
        for banned in ("subprocess", "urllib", "http", "requests", "ftplib", "smtplib", "telnetlib", "asyncio"):
            self.assertNotIn(banned, names)
        self.assertNotIn("os.system", SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn("os.popen", SCRIPT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
