"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getBridgeProc = getBridgeProc;
exports.ensureBridgeStarted = ensureBridgeStarted;
const vscode = require("vscode");
const child_process_1 = require("child_process");
let bridgeProc = null;
function getBridgeProc() {
    return bridgeProc;
}
async function ensureBridgeStarted(baseUrl) {
    const cfg = vscode.workspace.getConfiguration("jarvis.bridge");
    const autoStart = cfg.get("autoStart", true);
    if (!autoStart)
        return;
    // If already running, do nothing
    try {
        const ok = await fetch(`${baseUrl}/health`).then(r => r.ok).catch(() => false);
        if (ok)
            return;
    }
    catch {
        // ignore
    }
    // Start bridge using configured command OR VS Code python interpreter OR fallback "python"
    const cmdSetting = cfg.get("command", "").trim();
    let cmd = "";
    let args = [];
    if (cmdSetting) {
        // naive split: good enough for now, we can improve later with shell parsing
        const parts = cmdSetting.split(" ").filter(Boolean);
        cmd = parts[0];
        args = parts.slice(1);
    }
    else {
        // Try VS Code Python extension interpreter path if available
        // If not, fallback
        const python = await guessPythonExecutable();
        cmd = python;
        args = ["-m", "agent.ide_bridge"];
    }
    bridgeProc = (0, child_process_1.spawn)(cmd, args, {
        cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
        shell: true,
        env: process.env
    });
    const out = vscode.window.createOutputChannel("Jarvis IDE Bridge");
    out.show(true);
    out.appendLine(`[Jarvis] Starting bridge: ${cmd} ${args.join(" ")}`);
    bridgeProc.stdout.on("data", (d) => out.appendLine(d.toString()));
    bridgeProc.stderr.on("data", (d) => out.appendLine(d.toString()));
    // Give it a moment then verify health (we'll keep it simple)
    await new Promise((r) => setTimeout(r, 1200));
}
async function guessPythonExecutable() {
    // Prefer the Python extension if installed; otherwise fallback to "python"
    try {
        const pyExt = vscode.extensions.getExtension("ms-python.python");
        if (pyExt) {
            if (!pyExt.isActive)
                await pyExt.activate();
            // Modern Python extension exposes settings; simplest: use configured interpreter path setting
            const pyCfg = vscode.workspace.getConfiguration("python");
            const interp = pyCfg.get("defaultInterpreterPath");
            if (interp && interp.trim())
                return interp.trim();
        }
    }
    catch {
        // ignore
    }
    return "python";
}
//# sourceMappingURL=bridgeStart.js.map