# Jarvis IDE (VS Code adapter)

Local VS Code extension that talks to Jarvis IDE Bridge.

## Dev install
1. Open this folder in VS Code: `ide/vscode-jarvis`
2. Run:
   - `npm install`
   - `npm run compile`
3. Press `F5` to launch Extension Development Host.

## Settings (recommended)
In your workspace `.vscode/settings.json`:

```json
{
  "jarvis.bridge.autoStart": true,
  "jarvis.bridge.command": "conda run -n jarvis-agent python -m agent.ide_bridge",
  "jarvis.session.testCommand": "python -m compileall .",
  "jarvis.session.testTimeoutSeconds": 900
}
Commands
Jarvis: Ask (Natural Language)

Jarvis: Send Diagnostics

Jarvis: Apply Pending Patch

Jarvis: Discard Pending Patch

# How to run MK3.6 (test it)
1) In VS Code open folder: `ide/vscode-jarvis`
2) Terminal:
```bash
npm install
npm run compile
Press F5 → opens “Extension Development Host”

In the new VS Code window, open jarvis-agent repo

Run command palette:

Jarvis: Ask (Natural Language)
Try:
“Fix the NameError in workspace/ide/ide_test/broken.py and run checks.”

If it produces a patch:

Run Jarvis: Apply Pending Patch and type the phrase.

