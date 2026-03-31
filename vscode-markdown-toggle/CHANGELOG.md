# Changelog

All notable changes to Markdown Preview Toggle are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

---

## [1.2.0] — 2026-03-31 — Toggleable Light Theme

### Added
- New **`$(color-mode) Light`** status bar button (sits left of the preview button) to toggle the light theme on/off at runtime
- New VS Code setting `markdownToggle.lightPreview` (boolean, default: `true`) — can also be changed via Settings UI or `settings.json`
- `onDidChangeConfiguration` listener — reacts instantly if the setting is changed externally (e.g. directly in `settings.json`)
- Preview pane is automatically refreshed (close + reopen) when the theme setting changes, so the new style takes effect without manual reload
- New command: `Toggle Markdown Preview Light Theme` (accessible via Command Palette)

### Changed
- Light theme is now **opt-out** (default on) rather than always-on
- CSS injection moved from static `markdown.previewStyles` manifest entry to dynamic management of VS Code's `markdown.styles` global setting — allows runtime add/remove without restart
- `deactivate()` now cleans up the CSS entry from `markdown.styles` when the extension is disabled or uninstalled
- Command IDs and config namespace updated to `markdownToggle.*` for consistency

### Removed
- Static `contributes.markdown.previewStyles` entry — replaced by runtime `markdown.styles` management

---

## [1.1.0] — 2026-03-31 — Light Theme Preview

### Added
- `preview-light.css` — injected into the Markdown preview pane via the `markdown.previewStyles` contribution point
- Forces white background (`#ffffff`) and black text (`#000000`) in the preview pane regardless of the active VS Code colour theme
- Comprehensive element coverage: body, headings (h1–h6), paragraphs, links, inline code, code blocks, blockquotes, tables (with alternating row shading), horizontal rules, and `<mark>` highlights

---

## [1.0.1] — 2026-03-24 — Security Patch

### Fixed
- **SEC-01 — Race condition:** `previewOpen` state flag is now set *before* the `await executeCommand(...)` call to close the double-click race window. Added a `toggling` boolean lock with `try/finally` to prevent re-entrant command execution.
- **SEC-02 — Null dereference:** Added `if (!statusBarItem) return` guard in `updateButton()` to prevent a potential crash during edge-case teardown sequences.
- **State cleanup:** `toggling` flag is now also reset when the active editor changes, ensuring the lock cannot get stuck across file switches.

---

## [1.0.0] — 2026-03-24 — Initial Release

### Added
- Status bar button (`👁 MD Preview`) that appears automatically when editing any `.md` file
- One-click toggle to open the built-in Markdown preview side-by-side
- Click again to close the preview and return to single-editor layout
- Button highlights amber when preview is open; returns to default when closed
- Button auto-hides when switching to a non-Markdown file
- Lazy activation — extension only loads when a Markdown file is opened (zero startup overhead)
