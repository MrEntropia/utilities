#!/bin/zsh
# install.sh [--uninstall]
#
# Installs claude-iterm-recover for the current user:
#   ~/.local/bin/claude-tab, ~/.local/bin/claude-recover   (symlinks into this checkout)
#   ~/Library/Application Support/iTerm2/DynamicProfiles/claude-profiles.json
#       one "Claude · <name>" profile per line of profiles.conf, plus "Claude · Recover"
#   ~/Library/LaunchAgents/com.mrentropia.claude-recover.plist   (loaded)
#
# Reads ~/.config/claude-iterm-recover/profiles.conf; see profiles.conf.example.
set -eu

here=${0:A:h}
bin=$HOME/.local/bin
conf=$HOME/.config/claude-iterm-recover/profiles.conf
dyn="$HOME/Library/Application Support/iTerm2/DynamicProfiles/claude-profiles.json"
label=com.mrentropia.claude-recover
agent=$HOME/Library/LaunchAgents/$label.plist
recover_guid=A3C1F6E2-5B7D-4E19-9C2A-0D4F8B6E1C57

if [[ ${1:-} == --uninstall ]]; then
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$agent" "$dyn" "$bin/claude-tab" "$bin/claude-recover"
  print "removed. Markers in ~/.claude/iterm-sessions and $conf were left alone."
  exit 0
fi

for t in jq uuidgen osascript python3; do
  command -v $t >/dev/null || { print -u2 "install.sh: $t is required"; exit 1 }
done
[[ -x $HOME/.local/bin/claude ]] || print -u2 "warning: ~/.local/bin/claude not found; claude-tab expects the native Claude Code install there (or set CLAUDE_TAB_BIN)"
[[ -f $conf ]] || { print -u2 "install.sh: $conf missing. Copy profiles.conf.example there and edit it."; exit 1 }

# 1. scripts
mkdir -p "$bin"
ln -sfn "$here/bin/claude-tab" "$bin/claude-tab"
ln -sfn "$here/bin/claude-recover" "$bin/claude-recover"

# 2. iTerm2 dynamic profiles
mkdir -p "${dyn:h}"
python3 - "$conf" "$dyn" "$bin" "$recover_guid" <<'EOF'
import json, sys, uuid
conf, out, bin, recover_guid = sys.argv[1:]
profiles = []
for raw in open(conf):
    line = raw.strip()
    if not line or line.startswith('#'):
        continue
    parts = line.split('=')
    if len(parts) < 2:
        sys.exit(f"install.sh: bad line in {conf}: {line!r}")
    name, d = parts[0].strip(), parts[1].strip()
    guid = parts[2].strip().upper() if len(parts) > 2 and parts[2].strip() else \
        str(uuid.uuid5(uuid.NAMESPACE_URL, 'claude-iterm-recover:' + name)).upper()
    profiles.append({
        "Name": f"Claude · {name}",
        "Guid": guid,
        "Custom Directory": "Yes",
        "Working Directory": d,
        "Custom Command": "Yes",
        "Command": f"/bin/zsh -lc \"'{bin}/claude-tab' '{name}' '{d}'; exec /bin/zsh -il\"",
        "Tags": ["claude"],
    })
profiles.append({
    "Name": "Claude · Recover",
    "Guid": recover_guid,
    "Custom Directory": "No",
    "Custom Command": "Yes",
    "Command": f"/bin/zsh -lc \"'{bin}/claude-recover'; exec /bin/zsh -il\"",
    "Tags": ["claude"],
})
json.dump({"Profiles": profiles}, open(out, 'w'), indent=2, ensure_ascii=False)
open(out, 'a').write('\n')
print(f"wrote {len(profiles)} profiles to {out}")
EOF

# 3. login LaunchAgent
mkdir -p "${agent:h}" "$HOME/.claude/iterm-sessions"
sed "s#__HOME__#$HOME#g" "$here/launchd/$label.plist" > "$agent"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$agent"
print "loaded $label"

print "done. Open iTerm2 → Profiles → 'Claude · …' to start a session; 'Claude · Recover' or \`claude-recover\` brings back what ended abruptly."
