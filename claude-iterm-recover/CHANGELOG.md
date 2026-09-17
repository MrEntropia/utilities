# Changelog

## 1.0.0 (2026-09-17)

- `claude-tab`: launches Claude Code with Remote Control named after the project and a pre-chosen session id; marker lifecycle distinguishes a normal exit, a tab closed by hand, and everything abrupt (Cmd-Q / Restart, iTerm2 gone, `claude` killed by a signal, reboot).
- `claude-recover`: reopens abrupt sessions as `claude --resume … --remote-control`, one iTerm2 tab each in the original profile; skips sessions still running; `--list`, `--quiet`, per-uuid reopen from the closed set.
- Login LaunchAgent and `install.sh` (dynamic iTerm2 profiles from `profiles.conf`, `--uninstall`).
