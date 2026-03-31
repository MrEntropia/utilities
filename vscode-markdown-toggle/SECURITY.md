# Security Audit — Markdown Preview Toggle v1.2.0

**Audit Date:** March 2026
**Auditor:** Internal
**Extension Version Audited:** 1.2.0

---

## Scope

Full static analysis and logic review of:
- `extension.js` — main extension entry point
- `package.json` — manifest, permissions, and activation surface
- `preview-light.css` — stylesheet injected into Markdown preview pane

---

## Methodology

1. Manual code review
2. ESLint static analysis (no-eval, no-implied-eval, no-new-func)
3. `npm audit` dependency vulnerability scan
4. Custom pattern scan for dangerous Node.js APIs
5. Manifest permission surface audit
6. Logic and state safety review
7. CSS injection surface review
8. VS Code settings mutation safety review (added v1.2.0)

---

## Findings Summary

| ID | Severity | Status | Version | Description |
|---|---|---|---|---|
| SEC-01 | Medium | ✅ Fixed | 1.0.1 | Async race condition on rapid double-click |
| SEC-02 | Low | ✅ Fixed | 1.0.1 | Missing null guard in `updateButton()` |

No new issues found in v1.2.0. No critical or high severity issues have ever been identified.

---

## v1.2.0 New Surface: Settings Mutation

v1.2.0 adds the ability to modify VS Code's `markdown.styles` global setting at runtime. This surface was reviewed for the following risks:

### Settings Array Safety
The extension reads the current `markdown.styles` array before modifying it, filters only its own entry (matched by exact path), and writes back the result. It never replaces the entire array blindly.

```js
const styles = config.get('styles', []);
const filtered = styles.filter(s => s !== cssPath);
// Only our entry is ever removed — other entries preserved
await config.update('styles', filtered, vscode.ConfigurationTarget.Global);
```

### Path Safety
The CSS path is derived exclusively from `context.extensionPath` — a read-only value provided by VS Code at activation time. It is never constructed from user input, workspace content, or environment variables.

```js
function getLightCssPath(context) {
  return path.join(context.extensionPath, 'preview-light.css');
}
```

`path.join` is used (not string concatenation), and both components are controlled values. There is no path traversal risk.

### Deactivation Cleanup
`deactivate()` removes the extension's CSS entry from `markdown.styles` on uninstall or disable, ensuring no residue is left in the user's global settings.

### Configuration Scope
All settings writes use `vscode.ConfigurationTarget.Global` — the standard user-level scope. Workspace or folder-level settings are not modified.

---

## Detailed Historical Findings

### SEC-01 — Async Race Condition (Fixed in v1.0.1)

**Severity:** Medium | **Status:** Fixed

In v1.0.0, the `previewOpen` state flag was set *after* an `await executeCommand(...)` call. Rapid double-clicks could fire the command twice before state updated, opening two preview panels and desynchronising the toggle state.

**Fix (v1.0.1+):** State is set before `await`. A `toggling` boolean lock with `try/finally` blocks re-entry for the duration of the async operation.

---

### SEC-02 — Null Dereference in updateButton() (Fixed in v1.0.1)

**Severity:** Low | **Status:** Fixed

`updateButton()` accessed `statusBarItem` without an existence check, risking a runtime crash during edge-case teardown sequences.

**Fix (v1.0.1+):** `if (!statusBarItem) return` guard added. In v1.2.0, both `previewBtn` and `themeBtn` have equivalent guards.

---

## Clean Checks (No Issues Found)

### Code Execution
- No `eval()`, `new Function()`, or `setTimeout(string)` calls
- No dynamic `require()` — only `require('vscode')` and `require('path')` (Node.js built-ins)
- No `child_process` usage

### Network & Data
- No `fetch()`, `http`, `https`, or `XMLHttpRequest` calls
- No `WebSocket` or outbound connections of any kind
- No `process.env` access

### File System
- No `fs.*` calls — the extension reads no files and writes no files
- `path.join` is used only to construct the CSS path from two controlled values (`context.extensionPath` + `'preview-light.css'`)

### Manifest / Permissions
- Activation events scoped to `onLanguage:markdown` and the two named commands — extension does not load on startup
- No `*` or `onStartupFinished` activation
- No declared keybindings
- No menus injected into editor context menus
- No webview panels

### Commands
All `executeCommand` calls use hardcoded string literals:
```
'markdown.showPreviewToSide'
'workbench.action.closeEditorsInOtherGroups'
```
Neither is derived from user input or file content.

### CSS Surface
`preview-light.css` contains:
- No `<script>` tags or JavaScript
- No external resource references (`url()`, `@import`)
- Only colour, background, border, and font properties on standard HTML elements
- Static file bundled at package time — cannot be modified at runtime

---

## Dependency Audit

```
npm audit result: 0 vulnerabilities found
```

No `dependencies` or `devDependencies` are bundled into the `.vsix`. The only runtime dependencies are `vscode` and `path`, both provided by the VS Code / Node.js runtime.

---

## Conclusion

The v1.2.0 additions (theme toggle button, `markdownToggle.lightPreview` setting, dynamic `markdown.styles` management) introduce no new security vulnerabilities. The settings mutation pattern is safe — array entries are filtered by exact path match, the CSS path is derived from a read-only system value, and cleanup runs on deactivation. All previously identified issues remain resolved. The extension continues to pass all static analysis checks with zero vulnerabilities.
