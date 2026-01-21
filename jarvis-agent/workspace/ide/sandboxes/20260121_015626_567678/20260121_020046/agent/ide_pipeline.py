# agent/ide_pipeline.py
from __future__ import annotations

"""Workspace-agnostic IDE patch pipeline.

This pipeline is separate from agent/devtools.py:
- devtools.py is repo-scoped (Jarvis self-editing) and intentionally restricts paths.
- ide_pipeline.py operates on any workspace_root provided by an IDE client.

Safety model:
- All edits are first applied to a sandbox copy under jarvis-agent/workspace/ide/sandboxes/.
- Only an explicit typed confirmation applies edits to the real workspace.
- We refuse any path that escapes the workspace root.
"""

from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import re
import shutil
import subprocess
import difflib

from .models import load_model_roles
from .ide_store import (
    load_session,
    save_session,
    sandboxes_dir,
    backups_dir,
    logs_dir,
    _repo_root,  # type: ignore
)


# -------------------------
# Models (lazy global)
# -------------------------

_roles = load_model_roles()
_general_model, _coder_model, _research_model, _math_model, _science_model, _review_model = _roles


# -------------------------
# Utilities
# -------------------------


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_run_log(prefix: str, content: str) -> str:
    logs_dir().mkdir(parents=True, exist_ok=True)
    rid = _now_id()
    p = logs_dir() / f"{prefix}_{rid}.log"
    p.write_text(content or "", encoding="utf-8", errors="replace")
    # store as repo-relative for convenience
    try:
        return str(p.relative_to(_repo_root())).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def _run(cmd: List[str], cwd: Path, timeout: Optional[int] = None) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]} (is it installed and on PATH?)"
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return 1, "", f"Failed to run {' '.join(cmd)}: {e}"


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None

    # direct parse
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    # try to find first {...}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    snippet = text[start : end + 1]
    try:
        obj = json.loads(snippet)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _resolve_in_workspace(workspace_root: Path, user_path: str) -> Path:
    """Resolve a (relative or absolute) path into workspace_root safely."""
    user_path = (user_path or "").strip()
    if not user_path:
        raise ValueError("Empty path")

    # If absolute, keep it as-is but still enforce it lives under workspace_root
    p = Path(user_path)
    abs_path = p if p.is_absolute() else (workspace_root / _normalize_path(user_path))
    abs_path = abs_path.resolve()

    # Enforce confinement
    try:
        abs_path.relative_to(workspace_root.resolve())
    except Exception:
        raise ValueError(f"Path escapes workspace root: {user_path}")

    return abs_path


def _read_text_safe(path: Path, max_chars: int = 120_000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        t = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(t) > max_chars:
        return t[:max_chars] + "\n\n... (truncated) ...\n"
    return t


def _rg_available() -> bool:
    rc, _, _ = _run(["rg", "--version"], cwd=Path.cwd())
    return rc == 0


def _search_rg(root: Path, query: str, max_hits: int = 40) -> str:
    if not query.strip() or not _rg_available():
        return ""

    rc, out, err = _run(
        ["rg", "--line-number", "--no-heading", "--max-count", str(max_hits), query, str(root)],
        cwd=root,
        timeout=30,
    )
    merged = (out or "") + "\n" + (err or "")
    return merged.strip()


def _copy_workspace_to_sandbox(workspace_root: Path, sandbox: Path, excludes: Optional[List[str]] = None) -> None:
    """Copy the target workspace into a sandbox directory.

    Key safety fix:
    If the workspace_root contains Jarvis' IDE artifact folders (workspace/ide/*),
    do NOT copy sessions/sandboxes/backups/runs into the sandbox, or you'll get recursive paths.
    """
    if sandbox.exists():
        shutil.rmtree(sandbox)

    default_excludes = {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        ".next",
        ".cache",
        "dist",
        "build",
        "out",
        "coverage",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "bin",
        "obj",
        "target",
    }
    if excludes:
        default_excludes.update({x.strip() for x in excludes if isinstance(x, str) and x.strip()})

    workspace_root_res = workspace_root.resolve()

    # Detect whether Jarvis IDE artifacts live inside this workspace root
    jarvis_ide_inside = False
    try:
        from .ide_store import ide_root
        ide_root().resolve().relative_to(workspace_root_res)
        jarvis_ide_inside = True
    except Exception:
        jarvis_ide_inside = False

    def _ignore(dirpath: str, names: List[str]) -> set:
        ignore = set()

        # Standard excludes by name
        for n in names:
            if n in default_excludes:
                ignore.add(n)
            if n.lower() == "__pycache__" or n.lower().endswith(".pyc"):
                ignore.add(n)

        # Extra excludes ONLY when the workspace contains Jarvis IDE artifacts
        if jarvis_ide_inside:
            try:
                rel = Path(dirpath).resolve().relative_to(workspace_root_res).as_posix()
            except Exception:
                rel = ""

            # If we're inside any of these, ignore everything under them (prevents recursion)
            blocked_prefixes = (
                "workspace/ide/sandboxes",
                "workspace/ide/sessions",
                "workspace/ide/backups",
                "workspace/ide/runs",
            )
            if any(rel.startswith(p) for p in blocked_prefixes):
                return set(names)

            # If we're exactly at workspace/ide, ignore the internal dirs/files
            if rel == "workspace/ide":
                for n in names:
                    if n in {"sandboxes", "sessions", "backups", "runs"}:
                        ignore.add(n)
                    if n == "token.json":
                        ignore.add(n)

        return ignore

    shutil.copytree(workspace_root, sandbox, ignore=_ignore)



# -------------------------
# Planning + patch generation
# -------------------------


def _plan_changes(prompt: str, diagnostics: List[Dict[str, Any]], context_files: List[str]) -> Dict[str, Any]:
    """Use the review model to propose focus files + search queries.

    Returns a dict like:
      {"focus_files": ["src/a.py"], "search_queries": ["FooBar"], "test_command": "pytest"}
    """

    diag_text = json.dumps(diagnostics[:50], indent=2)
    msg = [
        "You are Jarvis IDE Planner.",
        "Return JSON ONLY (no markdown).",
        "Schema: {\"focus_files\": [string], \"search_queries\": [string], \"test_command\": string|null, \"notes\": string}",
        "Rules:",
        "- focus_files should be paths relative to the workspace root when possible.",
        "- search_queries should be short symbols/strings to look up in the codebase.",
        "- If unsure, include the diagnostic file paths.",
        f"User request: {prompt}",
        "Diagnostics:",
        diag_text,
        "Files already in context:",
        "\n".join(context_files[:30]),
        "JSON:",
    ]

    raw = _review_model.chat(msg, max_new_tokens=350, temperature=0.0, format="json")
    obj = _extract_first_json_object(raw) or {}

    focus_files = obj.get("focus_files")
    if not isinstance(focus_files, list):
        focus_files = []

    search_queries = obj.get("search_queries")
    if not isinstance(search_queries, list):
        search_queries = []

    test_command = obj.get("test_command")
    if not isinstance(test_command, str) or not test_command.strip():
        test_command = None

    notes = obj.get("notes")
    if not isinstance(notes, str):
        notes = ""

    return {
        "focus_files": [str(x) for x in focus_files if isinstance(x, str) and x.strip()][:20],
        "search_queries": [str(x) for x in search_queries if isinstance(x, str) and x.strip()][:12],
        "test_command": test_command,
        "notes": notes.strip()[:2000],
    }


def _build_coder_prompt(
    user_prompt: str,
    diagnostics: List[Dict[str, Any]],
    file_context: List[Tuple[str, str]],
    search_results: str,
) -> List[str]:
    """
    Build the prompt sent to the coder model.

    Key points:
    - We explicitly inject "Hard rules" (CODER_RULES) to prevent placeholder/guessy fixes.
    - We provide diagnostics + file contents + optional ripgrep hits.
    - We demand JSON-only output with a strict schema.
    """

    CODER_RULES = [
        "Make changes ONLY when justified by provided code/diagnostics.",
        "Do NOT use placeholder values or arbitrary strings just to silence errors.",
        "Prefer fixing the root cause by following existing project patterns.",
        "Keep edits minimal and localized; avoid reformatting unrelated code.",
        "If unsure, return files: [] with a summary asking for the missing context.",
        "Do NOT invent new files unless the user request requires it AND existing patterns justify it.",
        "Avoid broad refactors unless explicitly requested; focus on the failing area.",
    ]

    diag_text = json.dumps((diagnostics or [])[:80], indent=2)

    ctx_chunks: List[str] = []
    for path, content in (file_context or []):
        # Keep a clear delimiter so the model can reason per-file
        ctx_chunks.append(f"===== FILE: {path} =====\n{content}")

    ctx_text = "\n\n".join(ctx_chunks)

    # Normalize search results to a string and cap it
    sr = (search_results or "").strip()

    prompt: List[str] = [
        "You are Jarvis IDE Coder.",
        "Your job is to propose safe, minimal edits that fix the user's request.",
        "Return JSON ONLY (no markdown, no commentary, no code fences).",
        "Output schema (JSON object):",
        "{\"files\": [{\"path\": string, \"content\": string, \"delete\": boolean?}], \"summary\": string}",
        "Hard rules:",
        *[f"- {r}" for r in CODER_RULES],
        "Additional formatting rules:",
        "- 'files' must contain ONLY the files you changed.",
        "- Each 'content' must be the FULL final file content (not a diff).",
        "- 'path' should be relative to the workspace root whenever possible and use forward slashes.",
        "- Preserve existing formatting and style. Do not reformat unrelated code.",
        "- If you cannot safely fix with the provided context, return: {\"files\": [], \"summary\": \"...what you need...\"}",
        f"User request: {user_prompt}",
        "Diagnostics (may be empty):",
        diag_text,
        "Workspace file context (may be empty):",
        ctx_text[:180_000],
        "Search results (may be empty):",
        sr[:40_000],
        "Return JSON now:",
    ]

    return prompt



def _validate_files_payload(files: Any) -> List[Dict[str, Any]]:
    if not isinstance(files, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        p = item.get("path")
        c = item.get("content")
        delete_flag = bool(item.get("delete", False))
        if not isinstance(p, str) or not p.strip():
            continue
        if delete_flag:
            out.append({"path": p.strip(), "delete": True})
            continue
        if not isinstance(c, str):
            continue
        out.append({"path": p.strip(), "content": c})
    return out


# -------------------------
# Checks
# -------------------------


_ALLOWED_TEST_CMDS = {
    "python",
    "pytest",
    "ruff",
    "black",
    "npm",
    "pnpm",
    "yarn",
    "node",
    "tsc",
    "eslint",
    "dotnet",
    "mvn",
    "gradle",
    "go",
    "cargo",
}


def _split_command(cmd: str) -> List[str]:
    # Basic split (no shell). If you need quoting, pass array from client later.
    return [c for c in re.split(r"\s+", cmd.strip()) if c]


def _run_checks_in_sandbox(sandbox_root: Path, preferences: Dict[str, Any], planned_test_command: Optional[str]) -> Tuple[bool, str]:
    """Run deterministic checks in sandbox.

    Strategy:
    - If preferences.test_command exists -> use it.
    - Else if planned_test_command exists -> use it.
    - Else if python is present -> python -m compileall .
    - Else -> no-op (ok = True).
    """

    test_command = None
    pref_cmd = preferences.get("test_command")
    if isinstance(pref_cmd, str) and pref_cmd.strip():
        test_command = pref_cmd.strip()
    elif planned_test_command:
        test_command = planned_test_command.strip()

    # python fallback
    if not test_command:
        test_command = "python -m compileall ."

    parts = _split_command(test_command)
    exe = (parts[0] if parts else "").lower()
    if exe not in _ALLOWED_TEST_CMDS:
        # still safe because sandbox, but we keep an allowlist to avoid destructive commands
        return False, f"Blocked test command (not allowlisted): {exe}\nCommand: {test_command}"

    rc, out, err = _run(parts, cwd=sandbox_root, timeout=int(preferences.get("test_timeout_seconds", 900)))
    merged = (out or "") + "\n" + (err or "")
    ok = rc == 0
    return ok, merged.strip()


def _architect_gate(user_prompt: str, diagnostics: list, file_context: list, selection_text: str) -> Dict[str, Any]:
    """
    Returns:
      { "sufficient": bool, "message": str, "constraints": [str] }
    """
    # Hard guard first (deterministic)
    if not diagnostics and not file_context and not selection_text:
        return {
            "sufficient": False,
            "message": "I don't have enough context (no diagnostics, no file content, no selection). Send diagnostics and/or the active file buffer.",
            "constraints": []
        }

    # LLM gate (architect) – keeps Jarvis from guessing
    prompt = [
        "You are Jarvis IDE Architect. Decide if there is enough context to propose a code patch.",
        "Return JSON ONLY with keys: sufficient (bool), message (string), constraints (array of strings).",
        "Rules:",
        "- If you cannot justify a fix from the provided code/diagnostics, set sufficient=false and ask for the missing info.",
        "- Do NOT allow placeholder fixes (e.g., hardcoding random strings) unless that matches project intent.",
        f"User request: {user_prompt}",
        f"Diagnostics: {diagnostics[:50]}",
        f"Files provided: {[p for (p, _) in file_context]}",
        f"Selection text (may be empty): {selection_text[:800]}",
    ]

    raw = _review_model.chat(prompt, max_new_tokens=350, temperature=0.0, format="json")
    obj = _extract_first_json_object(raw) or {}

    return {
        "sufficient": bool(obj.get("sufficient", True)),
        "message": str(obj.get("message") or ""),
        "constraints": obj.get("constraints") if isinstance(obj.get("constraints"), list) else [],
    }
 

# -------------------------
# Public API
# -------------------------


def propose_patch(session_id: str, user_prompt: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Main entry: propose a patch (sandboxed) for the given session."""
    options = options or {}

    session = load_session(session_id)
    if not session:
        return {"error": "Unknown session", "details": session_id}

    workspace_root = Path(session.workspace_root).resolve()
    if not workspace_root.exists():
        return {"error": "Workspace root does not exist", "details": str(workspace_root)}

    preferences = session.preferences or {}

    # Build initial context file list from diagnostics + active file
    diag_files: List[str] = []
    for d in (session.diagnostics or [])[:80]:
        p = d.get("file") or d.get("path")
        if isinstance(p, str) and p.strip() and p not in diag_files:
            diag_files.append(p)

    active_file = None
    if isinstance(session.context, dict):
        af = session.context.get("active_file")
        if isinstance(af, str) and af.strip():
            active_file = af.strip()

    initial_files: List[str] = []
    if active_file:
        initial_files.append(active_file)
    for f in diag_files:
        if f not in initial_files:
            initial_files.append(f)

    plan = _plan_changes(user_prompt, session.diagnostics or [], initial_files)

    # Determine focus files
    focus_files = []
    for f in (plan.get("focus_files") or []):
        if f not in focus_files:
            focus_files.append(f)
    for f in initial_files:
        if f not in focus_files:
            focus_files.append(f)
    focus_files = focus_files[:15]

    # Load file contents (prefer IDE buffers if present)
    buffers = {}
    if isinstance(session.context, dict) and isinstance(session.context.get("buffers"), dict):
        buffers = session.context.get("buffers") or {}

    file_context: List[Tuple[str, str]] = []
    for f in focus_files:
        try:
            # If buffer exists, use it
            buf = buffers.get(f)
            if isinstance(buf, dict) and isinstance(buf.get("content"), str):
                file_context.append((f, str(buf.get("content"))))
                continue

            abs_path = _resolve_in_workspace(workspace_root, f)
            rel = str(abs_path.relative_to(workspace_root)).replace("\\", "/")
            file_context.append((rel, _read_text_safe(abs_path)))
        except Exception:
            continue

    # Optional semantic-ish search using rg
    search_blob = ""
    for q in (plan.get("search_queries") or [])[:10]:
        try:
            hits = _search_rg(workspace_root, q)
            if hits:
                search_blob += f"\n\n===== rg: {q} =====\n{hits}"
        except Exception:
            continue

    selection_text = ""
    if isinstance(session.context, dict):
        sel = session.context.get("selection")
        if isinstance(sel, dict):
            selection_text = str(sel.get("text") or "")

    arch = _architect_gate(user_prompt, session.diagnostics or [], file_context, selection_text)

    if not arch["sufficient"]:
        msg = arch["message"].strip() or "Not enough context to propose a safe patch."
        session.pending_patch = None
        session.history.append({
            "id": _now_id(),
            "when": datetime.now().isoformat(timespec="seconds"),
            "checks_ok": True,
            "summary": msg,
            "no_changes": True
        })
        save_session(session)
        return {"result": {"no_changes": True, "message": msg}}


    # Ask coder for file-based edits
    coder_prompt = _build_coder_prompt(user_prompt, session.diagnostics or [], file_context, search_blob)
    raw = _coder_model.chat(
        coder_prompt,
        max_new_tokens=int(options.get("max_new_tokens", 1200)),
        temperature=float(options.get("temperature", 0.2)),
        format="json",
    )
    obj = _extract_first_json_object(raw) or {}

    files = _validate_files_payload(obj.get("files"))
    summary = obj.get("summary") if isinstance(obj.get("summary"), str) else ""

    if not files:
    # Graceful no-op result (not an error)
        message = summary.strip() or "No changes needed based on the provided diagnostics/context."

        session.pending_patch = None
        session.history.append({
            "id": _now_id(),
            "when": datetime.now().isoformat(timespec="seconds"),
            "checks_ok": True,
            "summary": message,
            "no_changes": True
        })
        save_session(session)

        return {
            "result": {
                "no_changes": True,
                "message": message,
            }
        }


    # Create sandbox
    patch_id = _now_id()
    sandbox_root = sandboxes_dir() / session_id / patch_id
    sandbox_root.parent.mkdir(parents=True, exist_ok=True)

    _copy_workspace_to_sandbox(workspace_root, sandbox_root, excludes=preferences.get("copy_excludes"))

    # Apply edits in sandbox and build diff
    diffs: List[str] = []
    changed_paths: List[str] = []
    apply_notes: List[str] = []

    for f in files:
        rel = _normalize_path(f.get("path", ""))
        if not rel:
            continue

        # Resolve in workspace + sandbox (both must be safe)
        abs_real = _resolve_in_workspace(workspace_root, rel)
        abs_sandbox = _resolve_in_workspace(sandbox_root, rel)

        old_text = _read_text_safe(abs_real)

        if f.get("delete"):
            if abs_sandbox.exists() and abs_sandbox.is_file():
                abs_sandbox.unlink()
                apply_notes.append(f"deleted {rel}")
            else:
                apply_notes.append(f"delete skipped (missing) {rel}")
            new_text = ""
        else:
            new_text = str(f.get("content", ""))
            abs_sandbox.parent.mkdir(parents=True, exist_ok=True)
            abs_sandbox.write_text(new_text, encoding="utf-8", errors="replace")
            apply_notes.append(f"wrote {rel} ({len(new_text)} chars)")

        # Diff vs real workspace (what will change)
        old_lines = old_text.splitlines(True)
        new_lines = new_text.splitlines(True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        diff_text = "\n".join(list(diff)).strip()
        if diff_text:
            diffs.append(f"diff --git a/{rel} b/{rel}\n{diff_text}\n")
            changed_paths.append(rel)

    full_diff = "\n".join(diffs).strip() + "\n"

    # Run sandbox checks/tests
    ok_checks, checks_out = _run_checks_in_sandbox(sandbox_root, preferences, plan.get("test_command"))
    checks_log = _write_run_log(f"ide_checks_{session_id}", checks_out)

    # Review/explain using review model
    review_prompt = [
        "You are Jarvis IDE Reviewer.",
        "Explain what changed, why, and any remaining risks.",
        "Keep it concise but specific.",
        f"User request: {user_prompt}",
        f"Coder summary: {summary}",
        "Sandbox checks result:",
        f"OK={ok_checks}",
        (checks_out or "")[-6000:],
        "Patch diff (truncated):",
        full_diff[:60_000],
    ]
    review_text = _review_model.chat(review_prompt, max_new_tokens=600, temperature=0.0).strip()

    pending = {
        "id": patch_id,
        "when": datetime.now().isoformat(timespec="seconds"),
        "sandbox_root": str(sandbox_root),
        "changed_paths": changed_paths,
        "apply_notes": "\n".join(apply_notes),
        "diff": full_diff,
        "checks_ok": ok_checks,
        "checks_log": checks_log,
        "coder_summary": summary,
        "review": review_text,
    }

    session.pending_patch = pending
    session.history.append({"id": patch_id, "when": pending["when"], "checks_ok": ok_checks, "summary": summary})
    save_session(session)

    return {"result": {"pending_patch": pending}}


import shutil  # <-- add this near the top of ide_pipeline.py


def apply_pending_patch(session_id: str, confirm: str) -> Dict[str, Any]:
    """
    Applies the currently pending patch for a session to the REAL workspace.

    Safety:
    - Requires an exact typed confirmation phrase.
    - Only applies files that were changed in the sandbox (pending["changed_paths"]).
    - Backs up existing real files BEFORE writing.
    - Best-effort rollback if an apply step fails.

    Expected pending patch fields (created by propose_patch):
      pending = {
        "id": "...",
        "sandbox_root": "...",
        "changed_paths": ["rel/path1", "rel/path2", ...],
        ...
      }
    """
    s = load_session(session_id)
    if not s:
        return {"error": "Session not found.", "details": session_id}

    # ---- locate pending patch (support both storage styles) ----
    pending = None

    # Newer style (top-level field)
    if hasattr(s, "pending_patch"):
        pending = getattr(s, "pending_patch") or None

    # Older style (nested under last_run)
    if not pending:
        lr = getattr(s, "last_run", None)
        if isinstance(lr, dict):
            pending = lr.get("pending_patch") or None

    if not isinstance(pending, dict) or not pending:
        return {"error": "No pending patch for this session."}

    patch_id = str(pending.get("id") or "").strip()
    if not patch_id:
        return {"error": "Pending patch is missing an id."}

    # ---- typed confirmation ----
    expected = f"APPLY IDE PATCH {patch_id} I UNDERSTAND THIS MODIFIES THE WORKSPACE"
    if (confirm or "").strip() != expected:
        return {
            "error": "Typed confirmation required.",
            "details": f"Type exactly: {expected}",
        }

    # ---- validate workspace root ----
    root = Path(getattr(s, "workspace_root", "")).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"error": "Workspace root not found.", "details": str(root)}

    # ---- validate sandbox root ----
    sandbox_root = pending.get("sandbox_root")
    if not isinstance(sandbox_root, str) or not sandbox_root.strip():
        return {"error": "Pending patch is missing sandbox_root."}

    sandbox = Path(sandbox_root).expanduser().resolve()
    if not sandbox.exists() or not sandbox.is_dir():
        return {"error": "Sandbox root not found.", "details": str(sandbox)}

    # ---- validate changed paths ----
    changed_paths = pending.get("changed_paths") or []
    if not isinstance(changed_paths, list) or not changed_paths:
        return {"error": "Pending patch contains no changed_paths."}

    # ---- safe path resolver (prevents path traversal) ----
    def _safe_resolve_under(base: Path, rel: str) -> Path:
        rel = (rel or "").strip().replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel.startswith("/") or ":" in rel:
            raise ValueError(f"Invalid path: {rel!r}")
        parts = [p for p in rel.split("/") if p]
        if any(p == ".." for p in parts):
            raise ValueError(f"Path traversal not allowed: {rel!r}")
        abs_path = (base / rel).resolve()
        abs_path.relative_to(base.resolve())  # raises if escapes
        return abs_path

    # ---- create backup root (stored under Jarvis repo workspace/ide/backups/...) ----
    repo_root = Path(__file__).resolve().parent.parent  # jarvis-agent/
    backup_root = repo_root / "workspace" / "ide" / "backups" / s.id / patch_id
    backup_root.mkdir(parents=True, exist_ok=True)

    backup_notes: List[str] = []
    apply_notes: List[str] = []
    applied_rels: List[str] = []

    # ---- backup existing files BEFORE applying ----
    try:
        for rel in changed_paths:
            if not isinstance(rel, str) or not rel.strip():
                continue
            rel = rel.strip().replace("\\", "/")

            dst_real = _safe_resolve_under(root, rel)
            if dst_real.exists() and dst_real.is_file():
                dst_backup = backup_root / rel
                dst_backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst_real, dst_backup)
                backup_notes.append(f"backed up {rel}")
    except Exception as e:
        return {"error": "Failed to create backup.", "details": str(e)}

    # ---- apply patch by copying from sandbox -> real workspace ----
    # If a file was deleted in the sandbox, it won't exist there; delete it in real workspace too.
    try:
        for rel in changed_paths:
            if not isinstance(rel, str) or not rel.strip():
                continue
            rel = rel.strip().replace("\\", "/")

            src_sandbox = _safe_resolve_under(sandbox, rel)
            dst_real = _safe_resolve_under(root, rel)

            if src_sandbox.exists() and src_sandbox.is_file():
                dst_real.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_sandbox, dst_real)
                apply_notes.append(f"wrote {rel}")
                applied_rels.append(rel)
            else:
                # treat as delete
                if dst_real.exists() and dst_real.is_file():
                    dst_real.unlink()
                    apply_notes.append(f"deleted {rel}")
                    applied_rels.append(rel)
                else:
                    apply_notes.append(f"delete skipped (missing) {rel}")

    except Exception as e:
        # ---- rollback best-effort using backups ----
        try:
            for rel in applied_rels:
                backup_file = backup_root / rel
                real_file = _safe_resolve_under(root, rel)

                if backup_file.exists() and backup_file.is_file():
                    real_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, real_file)
                else:
                    # if no backup existed, remove newly created file if present
                    if real_file.exists() and real_file.is_file():
                        real_file.unlink()
        except Exception:
            pass

        return {"error": "Failed to apply patch to workspace.", "details": str(e)}

    applied_info = {
        "id": patch_id,
        "when": datetime.now().isoformat(timespec="seconds"),
        "notes": apply_notes,
        "backup_root": str(backup_root),
        "backup_notes": backup_notes,
    }

    # ---- clear pending patch so IDE UI doesn't keep showing apply state ----
    if hasattr(s, "pending_patch"):
        s.pending_patch = None

    lr = getattr(s, "last_run", None)
    if isinstance(lr, dict) and "pending_patch" in lr:
        lr["pending_patch"] = None

    # store last applied info (works whether or not IDESession has these fields)
    if hasattr(s, "last_applied"):
        s.last_applied = applied_info
    elif hasattr(s, "last_run"):
        lr = getattr(s, "last_run", None)
        if lr is None:
            lr = {}
            try:
                setattr(s, "last_run", lr)
            except Exception:
                lr = None
        if isinstance(lr, dict):
            lr["last_applied"] = applied_info

    # IMPORTANT: persist session updates so pending patch is cleared + last_applied stored
    try:
        save_session(s)
    except Exception as e:
        # Patch is already applied; failing to save session shouldn't pretend apply failed
        return {
            "result": {
                "applied": True,
                "applied_patch_id": patch_id,
                "notes": apply_notes,
                "backup_root": str(backup_root),
                "warning": f"Applied but failed to save session metadata: {e}",
            }
        }

    return {
        "result": {
            "applied": True,
            "applied_patch_id": patch_id,
            "notes": apply_notes,
            "backup_root": str(backup_root),
        }
    }





def discard_pending_patch(session_id: str) -> Dict[str, Any]:
    session = load_session(session_id)
    if not session:
        return {"error": "Unknown session", "details": session_id}

    session.pending_patch = None
    save_session(session)
    return {"result": {"discarded": True}}
