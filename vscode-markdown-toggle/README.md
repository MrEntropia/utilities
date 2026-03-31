# Markdown Preview Toggle - VS Code Extension

**Version:** 1.2.0
**Author:** vendor
**VS Code Minimum Version:** 1.75.0

---

## Overview

Markdown Preview Toggle adds two status bar buttons to VS Code:

- **`👁 MD Preview`** — opens and closes the Markdown preview pane side-by-side
- **`⊙ Light`** / **`⊙ Theme`** — toggles whether the preview renders in a forced light theme (white background, black text) or follows your active VS Code colour theme

Both buttons appear automatically when you open a `.md` file and hide when you switch away.

---

## Installation

> ⚠️ Do **not** double-click the `.vsix` file in Windows Explorer. That opens the Visual Studio installer, which is a separate product and cannot install VS Code extensions.

### Option 1 — Command Line (Recommended)

```bash
code --install-extension markdown-toggle-1.2.0.vsix
```

### Option 2 — VS Code UI

1. Open **Visual Studio Code**
2. Press `Ctrl+Shift+X` (Windows/Linux) or `Cmd+Shift+X` (macOS) to open the Extensions panel
3. Click the `···` menu in the top-right corner of the Extensions panel
4. Select **"Install from VSIX..."**
5. Navigate to and select `markdown-toggle-1.2.0.vsix`
6. Click **Install** when prompted
7. Reload VS Code if asked

### Option 3 — Drag & Drop

Drag the `.vsix` file directly into the VS Code window. A prompt will appear asking you to confirm installation.

---

## Uninstalling

1. Open the Extensions panel (`Ctrl+Shift+X`)
2. Search for **"Markdown Preview Toggle"**
3. Click the gear icon → **Uninstall**

Or via command line:

```bash
code --uninstall-extension vendor.markdown-toggle
```

On uninstall the extension automatically removes its CSS entry from your `markdown.styles` global setting, leaving no residue.

---

## Usage

### Preview toggle

1. Open any `.md` file in VS Code
2. The **`👁 MD Preview`** button appears in the bottom-right status bar
3. **Click once** — opens the rendered Markdown preview to the right; button highlights amber
4. **Click again** — closes the preview panel; button returns to normal

### Light theme toggle

The **`⊙ Light`** button (to the left of the preview button) controls the preview's colour theme:

| Button state | Meaning |
|---|---|
| `⊙ Light` (no highlight) | Light theme **active** — preview uses white background / black text |
| `⊙ Theme` (amber highlight) | Light theme **off** — preview follows your VS Code colour theme |

- **Click** to toggle between the two modes
- The preview refreshes immediately — no reload needed
- The setting persists across sessions

### Via Settings

You can also control the light theme via VS Code Settings:

1. Open Settings (`Ctrl+,`)
2. Search for `markdownToggle`
3. Toggle **"Force light theme in Markdown preview"**

Or edit `settings.json` directly:

```json
"markdownToggle.lightPreview": false
```

The status bar button updates instantly to reflect external setting changes.

---

## Features

- 🔘 **Two status bar buttons** — preview toggle and theme toggle, both scoped to Markdown files
- 👁 **Preview toggle** — open and close side-by-side preview with a single click
- ☀️ **Toggleable light theme** — force white/black in the preview, or revert to your editor theme
- 🔄 **Live refresh** — preview reloads automatically when the theme setting changes
- 🎛️ **Settings-aware** — `markdownToggle.lightPreview` setting works from both the button and Settings UI
- ⚡ **Lazy activation** — extension only loads when a Markdown file is opened (no startup overhead)
- 🔒 **Zero permissions** — no network access, no file system access, no shell commands
- 🧹 **Clean uninstall** — removes its `markdown.styles` entry automatically on deactivation

---

## How the Light Theme Works

When light theme is **on**, the extension adds the absolute path of `preview-light.css` to VS Code's global `markdown.styles` setting. When turned **off**, it removes that entry — leaving any other entries you may have untouched.

This is more flexible than the static `markdown.previewStyles` manifest approach (used in v1.1.0) because it can be added and removed at runtime without restarting VS Code.

---

## Security

This extension has been audited against the following threat categories:

| Check | Status |
|---|---|
| `eval()` / dynamic code execution | ✅ None |
| Network calls | ✅ None |
| File system access | ✅ None — CSS path is derived from `context.extensionPath` (read-only) |
| Shell / child process execution | ✅ None |
| `process.env` access | ✅ None |
| Broad activation (`*`) | ✅ Scoped to Markdown files and named commands only |
| User input passed to commands | ✅ All command strings are hardcoded literals |
| Webview / untrusted HTML rendering | ✅ None |
| Race condition (double-click) | ✅ Fixed in v1.0.1 — `toggling` lock with `try/finally` |
| Null dereference in status bar items | ✅ Null guards on both `previewBtn` and `themeBtn` |
| CSS external resource leakage | ✅ `preview-light.css` has no `url()` or `@import` references |
| `markdown.styles` pollution | ✅ Cleaned up on deactivation; only our own entry is ever removed |

See `SECURITY.md` for full details.

---

## Changelog

### v1.2.0 — Toggleable light theme
- New `⊙ Light` status bar button to toggle light theme on/off at runtime
- New `markdownToggle.lightPreview` setting (default: on) — also editable via Settings UI
- Preview auto-refreshes when theme setting changes
- CSS injection now managed dynamically via `markdown.styles`; cleaned up on deactivation

### v1.1.0 — Light theme preview
- Preview pane always renders white background / black text via injected `preview-light.css`

### v1.0.1 — Security patch
- Fixed async race condition on double-click (`toggling` lock)
- Added null guard in `updateButton()`

### v1.0.0 — Initial release
- Status bar toggle button for Markdown preview
- Auto-show/hide based on active file language

See `CHANGELOG.md` for full details.

---

## License

MIT License — free to use, modify, and distribute.
