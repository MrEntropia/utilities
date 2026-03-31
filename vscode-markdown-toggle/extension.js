const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

let previewBtn;
let themeBtn;
let previewOpen = false;
let toggling = false;

// ─── Light‑theme CSS (written to preview‑light.css at runtime) ──────────────

const LIGHT_CSS = `/*
  Markdown Preview Toggle — Force Light Theme
  Overrides VS Code's dark/high-contrast theme colors in the preview pane,
  ensuring black text on a white background regardless of the editor theme.
*/

:root {
  --vscode-editor-background: #ffffff !important;
  --vscode-editor-foreground: #000000 !important;
}

body {
  background-color: #ffffff !important;
  color: #000000 !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
  color: #111111 !important;
  border-bottom-color: #dddddd !important;
}

/* Paragraphs and inline text */
p, li, td, th, dt, dd, blockquote {
  color: #222222 !important;
}

/* Links */
a {
  color: #0060c0 !important;
}
a:hover {
  color: #003f8a !important;
}

/* Code blocks */
pre {
  background-color: #f5f5f5 !important;
  border: 1px solid #e0e0e0 !important;
  color: #1a1a1a !important;
}

code {
  background-color: #f0f0f0 !important;
  color: #c7254e !important;
}

pre code {
  background-color: transparent !important;
  color: #1a1a1a !important;
}

/* Blockquotes */
blockquote {
  background-color: #f9f9f9 !important;
  border-left: 4px solid #cccccc !important;
  color: #555555 !important;
}

/* Tables */
table {
  border-collapse: collapse;
}
th {
  background-color: #f0f0f0 !important;
  color: #111111 !important;
  border: 1px solid #cccccc !important;
}
td {
  border: 1px solid #dddddd !important;
  color: #222222 !important;
}
tr:nth-child(even) {
  background-color: #fafafa !important;
}

/* Horizontal rules */
hr {
  border-color: #dddddd !important;
}

/* Highlighted / marked text */
mark {
  background-color: #fff176 !important;
  color: #000000 !important;
}
`;

// ─── Helpers ────────────────────────────────────────────────────────────────

function getCssFilePath(context) {
  return path.join(context.extensionPath, 'preview-light.css');
}

function isLightThemeEnabled() {
  return vscode.workspace
    .getConfiguration('markdownToggle')
    .get('lightPreview', true);
}

function writeCssFile(context, enabled) {
  const cssPath = getCssFilePath(context);
  fs.writeFileSync(cssPath, enabled ? LIGHT_CSS : '/* light theme disabled */\n', 'utf8');
}

function cleanUpMarkdownStyles() {
  // Remove any stale entries our previous versions wrote into markdown.styles
  const config = vscode.workspace.getConfiguration('markdown');
  const styles = config.get('styles', []) || [];
  const cleaned = styles.filter(s => {
    if (!s || s.trim() === '') return false;
    if (/[/\\]mrentropia\.markdown-toggle-[^/\\]+[/\\]preview-light\.css$/i.test(s)) return false;
    return true;
  });
  if (cleaned.length !== styles.length) {
    config.update('styles', cleaned.length > 0 ? cleaned : undefined, vscode.ConfigurationTarget.Global);
  }
}

async function refreshPreview() {
  if (previewOpen) {
    await vscode.commands.executeCommand('workbench.action.closeEditorsInOtherGroups');
    await vscode.commands.executeCommand('markdown.showPreviewToSide');
  }
}

// ─── Button helpers ──────────────────────────────────────────────────────────

function updatePreviewBtn(open) {
  if (!previewBtn) return;
  if (open) {
    previewBtn.text = '$(eye-closed) MD Preview';
    previewBtn.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
  } else {
    previewBtn.text = '$(eye) MD Preview';
    previewBtn.backgroundColor = undefined;
  }
}

function updateThemeBtn(lightEnabled) {
  if (!themeBtn) return;
  if (lightEnabled) {
    themeBtn.text = '$(color-mode) Light';
    themeBtn.tooltip = 'Preview: Light theme active — click to use editor theme';
    themeBtn.backgroundColor = undefined;
  } else {
    themeBtn.text = '$(color-mode) Theme';
    themeBtn.tooltip = 'Preview: Using editor theme — click to force light theme';
    themeBtn.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
  }
}

// ─── Activate ────────────────────────────────────────────────────────────────

function activate(context) {

  // Clean up stale markdown.styles entries from previous versions (< 1.2.4)
  cleanUpMarkdownStyles();

  // Write CSS file based on current setting
  writeCssFile(context, isLightThemeEnabled());

  // -- Preview toggle button (right, priority 100)
  previewBtn = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  previewBtn.command = 'markdownToggle.togglePreview';
  previewBtn.tooltip = 'Toggle Markdown Preview';
  updatePreviewBtn(false);

  // -- Theme toggle button (right, priority 99 — sits just left of preview btn)
  themeBtn = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  themeBtn.command = 'markdownToggle.toggleLightTheme';
  updateThemeBtn(isLightThemeEnabled());

  // -- Command: toggle preview open/closed
  const togglePreviewCmd = vscode.commands.registerCommand(
    'markdownToggle.togglePreview',
    async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== 'markdown') {
        vscode.window.showInformationMessage('Open a Markdown file to use this toggle.');
        return;
      }
      if (toggling) return;
      toggling = true;
      try {
        if (!previewOpen) {
          previewOpen = true;
          updatePreviewBtn(true);
          await vscode.commands.executeCommand('markdown.showPreviewToSide');
        } else {
          previewOpen = false;
          updatePreviewBtn(false);
          await vscode.commands.executeCommand('workbench.action.closeEditorsInOtherGroups');
        }
      } finally {
        toggling = false;
      }
    }
  );

  // -- Command: toggle light theme on/off
  const toggleThemeCmd = vscode.commands.registerCommand(
    'markdownToggle.toggleLightTheme',
    async () => {
      const nowEnabled = isLightThemeEnabled();
      const next = !nowEnabled;

      await vscode.workspace
        .getConfiguration('markdownToggle')
        .update('lightPreview', next, vscode.ConfigurationTarget.Global);

      writeCssFile(context, next);
      updateThemeBtn(next);
      await refreshPreview();
    }
  );

  // -- React to setting changes made externally (e.g. settings.json edit)
  const onConfigChange = vscode.workspace.onDidChangeConfiguration(async (e) => {
    if (e.affectsConfiguration('markdownToggle.lightPreview')) {
      const enabled = isLightThemeEnabled();
      writeCssFile(context, enabled);
      updateThemeBtn(enabled);
      await refreshPreview();
    }
  });

  // -- Show/hide buttons based on active editor language
  const onEditorChange = vscode.window.onDidChangeActiveTextEditor((editor) => {
    if (editor && editor.document.languageId === 'markdown') {
      previewBtn.show();
      themeBtn.show();
    } else {
      previewBtn.hide();
      themeBtn.hide();
      previewOpen = false;
      toggling = false;
      updatePreviewBtn(false);
    }
  });

  // Show buttons immediately if a markdown file is already open
  const current = vscode.window.activeTextEditor;
  if (current && current.document.languageId === 'markdown') {
    previewBtn.show();
    themeBtn.show();
  }

  context.subscriptions.push(
    togglePreviewCmd,
    toggleThemeCmd,
    previewBtn,
    themeBtn,
    onEditorChange,
    onConfigChange
  );
}

// ─── Deactivate ───────────────────────────────────────────────────────────────

function deactivate() {
  if (previewBtn) previewBtn.dispose();
  if (themeBtn) themeBtn.dispose();
}

module.exports = { activate, deactivate };
