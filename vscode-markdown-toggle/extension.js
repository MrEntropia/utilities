const vscode = require('vscode');
const path = require('path');

let previewBtn;
let themeBtn;
let previewOpen = false;
let toggling = false;

// ─── Helpers ────────────────────────────────────────────────────────────────

function getLightCssPath(context) {
  return path.join(context.extensionPath, 'preview-light.css');
}

function isLightThemeEnabled() {
  return vscode.workspace
    .getConfiguration('markdownToggle')
    .get('lightPreview', true);
}

async function applyLightTheme(context) {
  const cssPath = getLightCssPath(context);
  const config = vscode.workspace.getConfiguration('markdown');
  const styles = config.get('styles', []);
  if (!styles.includes(cssPath)) {
    await config.update(
      'styles',
      [...styles, cssPath],
      vscode.ConfigurationTarget.Global
    );
  }
}

async function removeLightTheme(context) {
  const cssPath = getLightCssPath(context);
  const config = vscode.workspace.getConfiguration('markdown');
  const styles = config.get('styles', []);
  const filtered = styles.filter(s => s !== cssPath);
  if (filtered.length !== styles.length) {
    await config.update('styles', filtered, vscode.ConfigurationTarget.Global);
  }
}

async function refreshPreview() {
  // If preview is open, bounce it so the new CSS takes effect immediately
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

  // -- Preview toggle button (right, priority 100)
  previewBtn = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  previewBtn.command = 'markdownToggle.togglePreview';
  previewBtn.tooltip = 'Toggle Markdown Preview';
  updatePreviewBtn(false);

  // -- Theme toggle button (right, priority 99 — sits just left of preview btn)
  themeBtn = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  themeBtn.command = 'markdownToggle.toggleLightTheme';
  updateThemeBtn(isLightThemeEnabled());

  // Apply or remove light CSS on startup based on saved setting
  if (isLightThemeEnabled()) {
    applyLightTheme(context);
  } else {
    removeLightTheme(context);
  }

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

      // Persist the setting
      await vscode.workspace
        .getConfiguration('markdownToggle')
        .update('lightPreview', next, vscode.ConfigurationTarget.Global);

      // Apply or remove CSS from markdown.styles
      if (next) {
        await applyLightTheme(context);
      } else {
        await removeLightTheme(context);
      }

      updateThemeBtn(next);
      await refreshPreview();
    }
  );

  // -- React to setting changes made externally (e.g. settings.json edit)
  const onConfigChange = vscode.workspace.onDidChangeConfiguration(async (e) => {
    if (e.affectsConfiguration('markdownToggle.lightPreview')) {
      const enabled = isLightThemeEnabled();
      updateThemeBtn(enabled);
      if (enabled) {
        await applyLightTheme(context);
      } else {
        await removeLightTheme(context);
      }
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

async function deactivate(context) {
  // Clean up our CSS entry from markdown.styles on uninstall/disable
  if (context) await removeLightTheme(context);
  if (previewBtn) previewBtn.dispose();
  if (themeBtn) themeBtn.dispose();
}

module.exports = { activate, deactivate };
