# claude-iterm-recover

iTerm2 profiles that launch [Claude Code](https://code.claude.com) with Remote Control on, and a recovery tool that brings sessions back after the terminal went away without you closing it.

Open a "Claude · Rocket-scanner" tab and you get `claude --remote-control "Rocket-scanner"` in that project, with a conversation id chosen up front. Reboot the machine, force-quit iTerm2, or have `claude` die under you, and at next login every such session comes back in its own tab as `claude --resume <id> --remote-control <name>`, transcript included. Close a tab yourself and nothing comes back, which is the point.

## Pieces

| | |
|---|---|
| `bin/claude-tab <name> <dir> [uuid]` | Launcher the profiles call. Writes a marker in `~/.claude/iterm-sessions/` while the session runs; with a uuid it resumes instead of starting. |
| `bin/claude-recover [--quiet] [--list] [<uuid>…]` | Reopens every marker whose `claude` is no longer running, one iTerm2 tab each, in the original profile. Running sessions are skipped, so it is safe with other Claude tabs open. |
| `profiles.conf` | `<name>=<dir>[=<guid>]`, one profile per line. Lives in `~/.config/claude-iterm-recover/`. |
| `launchd/…plist` | Login agent: `claude-recover --quiet` 20 s after login. Does nothing, and does not launch iTerm2, when there is nothing to recover. |
| `install.sh` | Symlinks the scripts into `~/.local/bin`, generates the iTerm2 dynamic profiles, loads the agent. `--uninstall` reverses it. |

## What counts as abrupt

`claude-tab` decides from how it ends:

| ending | marker | recovered |
|---|---|---|
| `/exit`, Ctrl-C, `claude` returned normally | removed | no |
| tab closed by hand (SIGHUP, iTerm2 keeps running) | moved to `closed/` | no, but `claude-recover <uuid>` can, for 7 days |
| Cmd-Q, Restart, Shut Down (SIGHUP, iTerm2 gone within 3 s) | kept | yes |
| iTerm2 force-quit or crash | kept | yes |
| `claude` killed by a signal | kept, hint printed | yes |
| reboot, power loss (nothing runs) | kept | yes |

On SIGHUP the launcher watches for up to three seconds whether iTerm2 is still present before calling the close manual; if logout kills it first the marker is simply left, which is the recoverable outcome. iTerm2's own session restoration (`iTermServer`) keeps processes alive across an iTerm2 crash and reattaches them, so that case rarely reaches this tool; reboots are what it is for.

## Install

```sh
mkdir -p ~/.config/claude-iterm-recover
cp profiles.conf.example ~/.config/claude-iterm-recover/profiles.conf   # edit names and paths
./install.sh
```

Requires macOS with iTerm2, `jq`, and the native Claude Code install at `~/.local/bin/claude` (`CLAUDE_TAB_BIN` overrides that). iTerm2 picks up the dynamic profile file on its own; no restart needed.

If iTerm2 already has a profile you are replacing, put its GUID as the third field in `profiles.conf` so window arrangements and per-profile settings stay attached. Find it under Preferences → Profiles → Other Actions → Copy Profile as JSON.

## Day to day

- `claude-recover --list` shows what is running, what is recoverable, and what you closed by hand this week.
- `claude-recover` with no arguments is what the "Claude · Recover" profile runs.
- Log of the login agent: `~/.claude/iterm-sessions/recover.log`.
- A resumed session in a folder Claude Code has never trusted shows the "trust this folder?" prompt first; answering "No" exits normally and drops the marker, which is what you asked for.
