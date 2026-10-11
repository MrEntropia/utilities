#!/bin/sh
# install.sh [--archive DIR] [--uninstall]       macOS and Linux
#
# Finds Python 3.8+ (installing it if missing), then runs
#   claude_session_archive.py install [--archive DIR]
# which turns off transcript auto-deletion, adds the daily sync hook and runs a first sync.
# Also makes sure ~/.local/bin is on your PATH so `claude-session-archive` works in a new terminal.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

uninstall=0
for a in "$@"; do [ "$a" = --uninstall ] && uninstall=1; done

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

# macOS ships /usr/bin/python3 as a placeholder until the Command Line Tools are installed;
# running it then pops up an install dialog, so only touch it once xcode-select says they exist.
usable() {
  case "$1" in
    /usr/bin/python3|/usr/bin/python)
      if [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then return 1; fi ;;
  esac
  python_ok "$1"
}

find_python() {
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python /usr/bin/python3; do
    p=$(command -v "$c" 2>/dev/null) || continue
    if usable "$p"; then echo "$p"; return 0; fi
  done
  return 1
}

install_python() {
  echo "Python 3.8+ not found; installing it."
  case "$(uname)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        brew install python
      else
        echo "Starting Apple's Command Line Tools installer (includes Python 3)."
        echo "Finish it in the dialog that opens, then run this script again."
        xcode-select --install 2>/dev/null || true
        exit 1
      fi ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3
      elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y python3
      elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm python
      elif command -v zypper >/dev/null 2>&1; then sudo zypper install -y python3
      else echo "Install Python 3.8+ with your package manager, then run this again."; exit 1
      fi ;;
    *) echo "Unsupported system $(uname); on Windows run install.ps1."; exit 1 ;;
  esac
}

if ! py=$(find_python); then
  install_python
  py=$(find_python) || { echo "Python still not found after installing; open a new terminal and run this again."; exit 1; }
fi
echo "using $py ($("$py" --version 2>&1))"

if [ "$uninstall" = 1 ]; then
  exec "$py" "$here/claude_session_archive.py" uninstall
fi

# The hook keeps this exact path, so pass the stable one we found (a Homebrew upgrade
# removes versioned Cellar paths but keeps /opt/homebrew/bin/python3).
CLAUDE_SESSION_ARCHIVE_PYTHON="$py" "$py" "$here/claude_session_archive.py" install "$@"

# Put ~/.local/bin on PATH for future shells if it is not there already.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh)  rc=$HOME/.zshrc ;;
      */bash) if [ "$(uname)" = Darwin ]; then rc=$HOME/.bash_profile; else rc=$HOME/.bashrc; fi ;;
      */fish) rc= ; echo "fish: run  fish_add_path ~/.local/bin  once to use claude-session-archive" ;;
      *)      rc=$HOME/.profile ;;
    esac
    if [ -n "$rc" ] && ! grep -qs 'claude-session-archive: PATH' "$rc"; then
      printf '\n# claude-session-archive: PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
      echo "added ~/.local/bin to PATH in $rc (open a new terminal to use claude-session-archive)"
    fi ;;
esac
