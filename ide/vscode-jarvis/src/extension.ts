import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import { BridgeClient } from "./bridgeClient";
import { ensureBridgeStarted } from "./bridgeStart";

let client: BridgeClient | null = null;
let sessionId: string | undefined;

function normRelPathFromUri(uri: vscode.Uri) {
  return vscode.workspace.asRelativePath(uri).split("\\").join("/");
}

async function pushContext(): Promise<void> {
  if (!client || !sessionId) return;

  const editor = vscode.window.activeTextEditor;
  if (!editor) return;

  const rel = normRelPathFromUri(editor.document.uri);

  const sel = editor.selection;
  const selectedText = editor.document.getText(sel);

  const buffers: Record<string, any> = {};

  // always include active file buffer (even if not dirty)
  buffers[rel] = {
    content: editor.document.getText(),
    languageId: editor.document.languageId,
    version: editor.document.version,
  };

  // include other dirty buffers too (very useful)
  for (const doc of vscode.workspace.textDocuments) {
    if (!doc.isDirty) continue;
    const r = normRelPathFromUri(doc.uri);
    buffers[r] = { content: doc.getText(), languageId: doc.languageId, version: doc.version };
  }

  const payload = {
    active_file: rel,
    selection: {
      start: { line: sel.start.line, character: sel.start.character },
      end: { line: sel.end.line, character: sel.end.character },
      text: selectedText,
    },
    buffers,
  };

  await client.setContext(sessionId, payload);
}

function readTokenFromJarvisWorkspace(jarvisRepoRoot: string): string {
  const tokenPath = path.join(jarvisRepoRoot, "workspace", "ide", "token.json");
  const raw = fs.readFileSync(tokenPath, "utf8");
  return JSON.parse(raw).token;
}

function getWorkspaceRoot(): string | null {
  const wf = vscode.workspace.workspaceFolders?.[0];
  return wf?.uri.fsPath ?? null;
}

async function ensureSession(): Promise<void> {
  const wsRoot = getWorkspaceRoot();
  if (!wsRoot || !client) return;

  const cfg = vscode.workspace.getConfiguration();
  const testCommand = cfg.get<string>("jarvis.session.testCommand", "");
  const testTimeoutSeconds = cfg.get<number>("jarvis.session.testTimeoutSeconds", 900);

  const res = await client.startSession(wsRoot, "vscode", {
    test_command: testCommand || undefined,
    test_timeout_seconds: testTimeoutSeconds
  });

  // Support both shapes: {session_id} or {result:{session_id}}
  sessionId = res.session_id ?? res?.result?.session_id ?? null;
  if (!sessionId) throw new Error("Failed to get session_id from bridge.");

  await pushContext(); // initial
}

async function withSessionRetry<T>(op: () => Promise<T>): Promise<T> {
  try {
    return await op();
  } catch (e: any) {
    const msg = String(e?.message ?? e);

    // If bridge says session not found (commonly 404), recreate session once
    if (msg.includes("404") && msg.toLowerCase().includes("session")) {
      sessionId = undefined;
      await ensureSession();          // creates a new sessionId
      return await op();              // retry once
    }
    throw e;
  }
}


async function pushDiagnostics(): Promise<void> {
  if (!client || !sessionId) return;

  const all = vscode.languages.getDiagnostics();
  const diags: any[] = [];

  for (const [uri, items] of all) {
    const rel = vscode.workspace.asRelativePath(uri.fsPath);
    for (const d of items) {
      diags.push({
        file: rel,
        severity: d.severity === vscode.DiagnosticSeverity.Error ? "error"
          : d.severity === vscode.DiagnosticSeverity.Warning ? "warning"
          : "info",
        message: d.message,
        start_line: d.range.start.line + 1,
        end_line: d.range.end.line + 1
      });
    }
  }

  await client.setDiagnostics(sessionId, diags);
}

async function askJarvis(): Promise<void> {
  if (!client || !sessionId) return;

  await pushContext();
  await pushDiagnostics();

  const prompt = await vscode.window.showInputBox({
    title: "Ask Jarvis (Natural Language)",
    placeHolder: "e.g. Fix these errors, refactor this file, add tests..."
  });
  if (!prompt) return;

  const job = await client.request(sessionId, prompt, { max_new_tokens: 1200, temperature: 0.2 });
  const jobId = job.job_id ?? job?.result?.job_id;
  if (!jobId) {
    vscode.window.showErrorMessage("Jarvis did not return a job_id.");
    return;
  }

  const out = vscode.window.createOutputChannel("Jarvis IDE");
  out.show(true);
  out.appendLine(`[Jarvis] Job started: ${jobId}`);
  out.appendLine(`[Jarvis] Prompt: ${prompt}`);

  // Poll until done
  while (true) {
    await new Promise((r) => setTimeout(r, 1500));
    const j = await client.job(jobId);
    const status = j?.result?.status ?? j?.status;
    out.appendLine(`[Jarvis] status=${status}`);
    if (status !== "running") {
      out.appendLine(JSON.stringify(j, null, 2));
      break;
    }
  }

  // Fetch status to see pending patch
  const st = await client.status(sessionId);
  const pending = st?.result?.pending_patch ?? st?.pending_patch ?? null;

  if (pending && pending.id) {
    out.appendLine(`\n[Jarvis] Pending patch: ${pending.id}`);
    out.appendLine(pending.diff ?? "");
    vscode.window.showInformationMessage(`Jarvis produced a pending patch: ${pending.id}`);
  } else if (st?.result?.has_pending_patch === false) {
    vscode.window.showInformationMessage("Jarvis: no pending patch (no changes needed or error).");
  }
}

async function applyPendingPatch(): Promise<void> {
  if (!client || !sessionId) return;

  const st = await client.status(sessionId);
  const pending = st?.result?.pending_patch ?? null;

  if (!pending?.id) {
    vscode.window.showInformationMessage("No pending patch to apply.");
    return;
  }

  const phrase = `APPLY IDE PATCH ${pending.id} I UNDERSTAND THIS MODIFIES THE WORKSPACE`;
  const confirm = await vscode.window.showInputBox({
    title: "Apply patch confirmation",
    prompt: `Type exactly:\n${phrase}`,
    placeHolder: phrase
  });

  if (!confirm) return;

  const res = await client.apply(sessionId, confirm);
  vscode.window.showInformationMessage(`Apply result: ${res?.result?.applied ? "applied" : "unknown"}`);
}

async function discardPendingPatch(): Promise<void> {
  if (!client || !sessionId) return;
  await client.discard(sessionId);
  vscode.window.showInformationMessage("Pending patch discarded.");
}

export async function activate(context: vscode.ExtensionContext) {
  const wsRoot = getWorkspaceRoot();
  if (!wsRoot) return;

  const cfg = vscode.workspace.getConfiguration("jarvis.bridge");
  const baseUrl = cfg.get<string>("baseUrl", "http://127.0.0.1:8765");

  // If you are running the extension inside jarvis-agent repo, token is local.
  // For other projects, we will add a setting to point to Jarvis repo root (MK3.6.1).
  const jarvisRepoRoot = wsRoot.includes("jarvis-agent") ? wsRoot : null;
  if (!jarvisRepoRoot) {
    vscode.window.showWarningMessage("Jarvis IDE: Open the jarvis-agent repo first (MK3.6.1 will support external projects).");
    return;
  }

  await ensureBridgeStarted(baseUrl);

  const token = readTokenFromJarvisWorkspace(jarvisRepoRoot);
  client = new BridgeClient({ baseUrl, token });

  await ensureSession();

  context.subscriptions.push(
    vscode.commands.registerCommand("jarvis.openChat", askJarvis),
    vscode.commands.registerCommand("jarvis.sendDiagnostics", async () => {
      await pushDiagnostics();
      vscode.window.showInformationMessage("Jarvis: diagnostics sent.");
    }),
    vscode.commands.registerCommand("jarvis.applyPatch", applyPendingPatch),
    vscode.commands.registerCommand("jarvis.discardPatch", discardPendingPatch)
  );

  // Update context when active editor changes
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(async () => {
      try { await pushContext(); } catch {}
    }),
    vscode.window.onDidChangeTextEditorSelection(async () => {
      try { await pushContext(); } catch {}
    })
  );

  vscode.window.showInformationMessage("Jarvis IDE extension active.");
}

export function deactivate() {}
