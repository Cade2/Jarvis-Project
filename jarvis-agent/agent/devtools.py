# agent/devtools.py
from __future__ import annotations

"""Dev Mode sandbox + patch pipeline (agent-side).

Key design goals:
- Never write outside repo/ except inside workspace/.
- Prefer file-based edits (full file contents) so we DON'T depend on `git`.
- Still save a unified diff for transparency/auditability.
- Only `dev.apply_patch` can touch the real repo (CRITICAL).
"""

from typing import Dict, Any, Tuple, List
from pathlib import Path
from datetime import datetime
import json
import shutil
import subprocess
import os
import difflib


# -------------------------
# Paths + state helpers
# -------------------------


def _run(cmd: List[str], cwd: Path) -> Tuple[int, str, str]:
    """Run a subprocess safely.

    Returns: (returncode, stdout, stderr)
    Never raises FileNotFoundError (we convert it into a clean error message).
    """
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            shell=False,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]} (is it installed and on PATH?)"
    except Exception as e:
        return 1, "", f"Failed to run command {cmd!r}: {e}"


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _repo_root() -> Path:
    # agent/devtools.py -> agent/ -> repo root
    return Path(__file__).resolve().parent.parent


def _workspace_root() -> Path:
    return _repo_root() / "workspace"


def _sandbox_root() -> Path:
    # Allow override to avoid Windows MAX_PATH issues if needed
    # Example: set JARVIS_SANDBOX_DIR=C:\sb
    override = os.environ.get("JARVIS_SANDBOX_DIR", "").strip()
    if override:
        return Path(override)
    return _workspace_root() / "repo_sandbox"


def _patches_dir() -> Path:
    return _workspace_root() / "patches"


def _runs_dir() -> Path:
    return _workspace_root() / "runs"


def _state_path() -> Path:
    return _workspace_root() / "state.json"


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {
            "pending_patch": None,
            "last_test": None,
            "sandbox_path": str(_sandbox_root()),
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {
            "pending_patch": None,
            "last_test": None,
            "sandbox_path": str(_sandbox_root()),
        }


def _save_state(state: Dict[str, Any]) -> None:
    _workspace_root().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _ensure_workspace_layout() -> None:
    _workspace_root().mkdir(parents=True, exist_ok=True)
    _patches_dir().mkdir(parents=True, exist_ok=True)
    _runs_dir().mkdir(parents=True, exist_ok=True)


def _git_available() -> bool:
    rc, _, _ = _run(["git", "--version"], cwd=_repo_root())
    return rc == 0


def _copy_repo_to_sandbox() -> None:
    """Recreate sandbox as a clean copy of the repo.

    Excludes workspace/, logs/, .git, caches.
    """
    root = _repo_root()
    sandbox = _sandbox_root()

    if sandbox.exists():
        shutil.rmtree(sandbox)

    def _ignore(dirpath: str, names: List[str]) -> set:
        ignore = set()
        base = os.path.basename(dirpath).lower()

        # Always ignore VCS + caches
        for n in names:
            nl = n.lower()
            if nl in {".git", ".idea", ".vscode"}:
                ignore.add(n)
            if nl == "__pycache__":
                ignore.add(n)
            if nl.endswith(".pyc"):
                ignore.add(n)

        # Ignore root-level folders we don't want duplicated
        if Path(dirpath).resolve() == root.resolve():
            for n in names:
                nl = n.lower()
                if nl in {"workspace", "logs"}:
                    ignore.add(n)

        # Defensive: do not copy any workspace folders anywhere
        if base == "workspace" or base == "logs":
            return set(names)

        return ignore

    shutil.copytree(root, sandbox, ignore=_ignore)


def _run_compileall(cwd: Path) -> Tuple[bool, str]:
    """Deterministic checks (start simple): compileall."""
    cmd = ["python", "-m", "compileall", "."]
    rc, out, err = _run(cmd, cwd=cwd)
    merged = (out or "") + "\n" + (err or "")
    ok = (rc == 0)
    return ok, merged.strip()

def _run_smoke_import(cwd: Path) -> Tuple[bool, str]:
    """Small runtime check: verify agent imports (catches runtime issues)."""
    cmd = ["python", "-c", "import agent, agent.core; print('smoke ok')"]
    rc, out, err = _run(cmd, cwd=cwd)
    merged = (out or "") + "\n" + (err or "")
    return (rc == 0), merged.strip()


def _run_checks(cwd: Path) -> Tuple[bool, str]:
    ok_c, out_c = _run_compileall(cwd)
    ok_s, out_s = _run_smoke_import(cwd)
    ok = ok_c and ok_s
    combined = f"[compileall]\n{out_c}\n\n[smoke_import]\n{out_s}".strip()
    return ok, combined


def _write_run_log(prefix: str, content: str) -> str:
    run_id = _now_id()
    p = _runs_dir() / f"{prefix}_{run_id}.log"
    p.write_text(content or "", encoding="utf-8", errors="replace")
    return str(p.relative_to(_repo_root())).replace("\\", "/")


# -------------------------
# Patch building + applying
# -------------------------


_ALLOWED_PATCH_PREFIXES = ("agent/", "runner/", "config/")
_ALLOWED_SINGLE_FILES = ("cli.py",)


def _normalize_rel_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_allowed_patch_path(rel: str) -> bool:
    if not rel or rel.startswith("/") or ":" in rel:
        return False
    if ".." in rel.split("/"):
        return False
    if rel.startswith(("workspace/", "logs/", ".git/")):
        return False
    return rel.startswith(_ALLOWED_PATCH_PREFIXES) or rel in _ALLOWED_SINGLE_FILES


def _diff_one_file(sandbox: Path, rel: str, new_content: str) -> str:
    rel = _normalize_rel_path(rel)
    if not _is_allowed_patch_path(rel):
        raise ValueError(f"Disallowed path: {rel}")

    target = sandbox / rel
    old_text = ""
    if target.exists() and target.is_file():
        old_text = target.read_text(encoding="utf-8", errors="replace")

    old_lines = old_text.splitlines(True)
    new_lines = (new_content or "").splitlines(True)

    header = f"diff --git a/{rel} b/{rel}\n"
    fromfile = f"a/{rel}" if target.exists() else "/dev/null"
    tofile = f"b/{rel}"

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )

    if not diff_lines:
        return ""
    return header + "\n".join(diff_lines) + "\n"


def _build_diff_from_files(sandbox: Path, files: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for f in files:
        rel = _normalize_rel_path(f.get("path", ""))
        content = f.get("content", "")
        out.append(_diff_one_file(sandbox, rel, content))
    return "".join([d for d in out if d.strip()])


def _apply_files_to_dir(base_dir: Path, files: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Apply file edits by writing full contents.

    This is our primary apply path (no `git` required).
    """
    notes: List[str] = []
    for f in files:
        if not isinstance(f, dict):
            continue

        rel = _normalize_rel_path(str(f.get("path", "")))
        if not _is_allowed_patch_path(rel):
            return False, f"Disallowed path: {rel}"

        delete_flag = bool(f.get("delete", False))
        abs_path = (base_dir / rel).resolve()

        # Safety: ensure stays within base_dir
        try:
            abs_path.relative_to(base_dir.resolve())
        except Exception:
            return False, f"Path escapes sandbox: {rel}"

        if delete_flag:
            if abs_path.exists() and abs_path.is_file():
                abs_path.unlink()
                notes.append(f"deleted {rel}")
            else:
                notes.append(f"delete skipped (missing) {rel}")
            continue

        content = f.get("content", "")
        if not isinstance(content, str):
            return False, f"Invalid content for {rel} (must be a string)"

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8", errors="replace")
        notes.append(f"wrote {rel} ({len(content)} chars)")

    return True, "\n".join(notes)


def _diff_paths_are_allowed(diff_text: str) -> Tuple[bool, str]:
    paths = set()
    for line in (diff_text or "").splitlines():
        if line.startswith("+++ b/"):
            rel = line[len("+++ b/") :].strip()
            if rel and rel != "/dev/null":
                paths.add(rel)
    for p in paths:
        p = _normalize_rel_path(p)
        if not _is_allowed_patch_path(p):
            return False, f"Disallowed path in diff: {p}"
    return True, ""


def _backup_changed_files(repo_dir: Path, diff_text: str, backup_root: Path) -> None:
    """Backup any files mentioned by the diff (very simple parser)."""
    backup_root.mkdir(parents=True, exist_ok=True)
    changed = set()

    for line in (diff_text or "").splitlines():
        if line.startswith("+++ b/"):
            rel = line[len("+++ b/") :].strip()
            if rel and rel != "/dev/null":
                changed.add(_normalize_rel_path(rel))

    for rel in changed:
        src = (repo_dir / rel).resolve()
        if src.exists() and src.is_file():
            dst = (backup_root / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _apply_patch_with_git_apply(base_dir: Path, diff_path: Path) -> Tuple[bool, str]:
    """Apply a unified diff using git apply."""
    if not _git_available():
        return False, "git not found. Install Git for Windows or add it to PATH."
    rc, out, err = _run(["git", "apply", "--whitespace=nowarn", str(diff_path)], cwd=base_dir)
    merged = (out or "") + "\n" + (err or "")
    return (rc == 0), merged.strip()


# -------------------------
# Public tool functions
# -------------------------


def dev_status(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Show dev pipeline state."""
    _ensure_workspace_layout()
    state = _load_state()

    sandbox_exists = _sandbox_root().exists()
    return {
        "result": {
            "sandbox_exists": sandbox_exists,
            "sandbox_path": str(_sandbox_root()),
            "pending_patch": state.get("pending_patch"),
            "last_test": state.get("last_test"),
        }
    }


def dev_sandbox_reset(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Re-copy repo -> sandbox and run checks."""
    _ensure_workspace_layout()
    try:
        _copy_repo_to_sandbox()
    except Exception as e:
        return {"error": "Failed to build sandbox.", "details": str(e)}

    ok, out = _run_checks(_sandbox_root())
    run_log = _write_run_log("sandbox_reset_checks", out)
    


    state = _load_state()
    state["pending_patch"] = None
    state["last_test"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "check": "compileall",
        "log": run_log,
        "target": "sandbox",
    }
    _save_state(state)

    return {"result": {"sandbox_reset": True, "compileall_ok": ok, "run_log": run_log}}


def dev_propose_patch(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Apply a patch to the SANDBOX only, run checks, store as pending."""
    params = params or {}
    desc = (params.get("description") or "").strip()
    diff_text = (params.get("diff") or "").strip()
    files = params.get("files")

    _ensure_workspace_layout()

    sandbox = _sandbox_root()
    if not sandbox.exists():
        # Reduce friction: build sandbox automatically if missing
        try:
            _copy_repo_to_sandbox()
        except Exception as e:
            return {"error": "Sandbox does not exist and could not be created.", "details": str(e)}

    # Build diff from file edits if provided
    if (not diff_text) and files is not None:
        if not isinstance(files, list):
            return {"error": "Invalid 'files' (must be a list)."}
        try:
            diff_text = _build_diff_from_files(sandbox, files)
        except Exception as e:
            return {"error": "Failed to build diff from files.", "details": str(e)}

    if not diff_text:
        return {"error": "Missing diff. Provide 'diff' or 'files'."}

    ok_paths, why = _diff_paths_are_allowed(diff_text)
    if not ok_paths:
        return {"error": "Patch contains disallowed paths.", "details": why}

    patch_id = _now_id()

    # Save patch files for audit
    patch_rel = Path("workspace") / "patches" / f"pending_{patch_id}.diff"
    patch_path = (_repo_root() / patch_rel).resolve()
    if not diff_text.endswith("\n"):
        diff_text += "\n"
    _write_text(patch_path, diff_text)

    files_rel = None
    if isinstance(files, list) and files:
        files_rel = Path("workspace") / "patches" / f"pending_{patch_id}.json"
        files_path = (_repo_root() / files_rel).resolve()
        _write_text(files_path, json.dumps({"description": desc, "files": files}, indent=2))

    # --- Apply to SANDBOX ---
    applied_notes = ""
    if isinstance(files, list) and files:
        ok_apply, applied_notes = _apply_files_to_dir(sandbox, files)
        if not ok_apply:
            return {"error": "Failed to apply file edits to sandbox.", "details": applied_notes}
    else:
        # diff-only mode (manual paste) requires git
        if not _git_available():
            return {
                "error": "git is required to apply raw diffs, but was not found.",
                "details": "Install Git for Windows or use file-based patches via the coder model.",
            }

        # Check + apply
        rc, _, err = _run(["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)], cwd=sandbox)
        if rc != 0:
            return {"error": "Patch check failed (git apply --check).", "details": err.strip() or "No output."}

        rc, _, err = _run(["git", "apply", "--whitespace=nowarn", str(patch_path)], cwd=sandbox)
        if rc != 0:
            return {"error": "Failed to apply patch to sandbox (git apply).", "details": err.strip() or "No output."}

    # --- Checks ---
    ok, compile_out = _run_checks(sandbox)
    run_log = _write_run_log(f"propose_patch_checks_{patch_id}", compile_out)


    state = _load_state()
    state["pending_patch"] = {
        "id": patch_id,
        "diff_path": str(patch_rel).replace("\\", "/"),
        "files_path": str(files_rel).replace("\\", "/") if files_rel else None,
        "description": desc,
        "when": datetime.now().isoformat(timespec="seconds"),
        "sandbox_compileall_ok": ok,
        "compile_log": run_log,
        "apply_notes": applied_notes,
    }
    state["last_test"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "check": "compileall",
        "log": run_log,
        "target": "sandbox",
    }
    _save_state(state)

    return {
        "result": {
            "pending_patch": state["pending_patch"],
            "compileall_ok": ok,
            "compileall_output_tail": compile_out[-4000:] if isinstance(compile_out, str) else "",
            "run_log": run_log,
        }
    }


def dev_discard_patch(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Discard pending patch and reset sandbox."""
    _ensure_workspace_layout()
    if _sandbox_root().exists():
        shutil.rmtree(_sandbox_root())
    _copy_repo_to_sandbox()

    state = _load_state()
    state["pending_patch"] = None
    _save_state(state)
    return {"result": {"discarded": True, "sandbox_reset": True}}


def dev_apply_patch(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Apply the pending patch to the REAL repo (CRITICAL).

    If we have a pending files JSON, we apply via file writes (no git required).
    Otherwise, we apply via git apply.
    """
    _ensure_workspace_layout()
    state = _load_state()
    pending = state.get("pending_patch")
    if not pending:
        return {"error": "No pending patch to apply. Use dev.propose_patch first."}
    
    params = params or {}
    patch_id = (pending.get("id") or "").strip()
    if not patch_id:
        return {"error": "Pending patch missing id."}

    expected = f"APPLY PATCH {patch_id} I UNDERSTAND THIS MODIFIES THE REPO"
    if (params.get("confirm") or "").strip() != expected:
        return {"error": "Typed confirmation required.", "details": f"Type exactly: {expected}"}


    diff_rel = pending.get("diff_path")
    if not diff_rel:
        return {"error": "Pending patch missing diff_path."}

    diff_path = (_repo_root() / diff_rel).resolve()
    if not diff_path.exists():
        return {"error": f"Diff file not found: {diff_path}"}

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    ok_paths, why = _diff_paths_are_allowed(diff_text)
    if not ok_paths:
        return {"error": "Pending diff contains disallowed paths.", "details": why}

    backup_root = _patches_dir() / "backups" / patch_id
    _backup_changed_files(_repo_root(), diff_text, backup_root)

    # Apply
    files_rel = pending.get("files_path")
    if files_rel:
        files_path = (_repo_root() / files_rel).resolve()
        if not files_path.exists():
            return {"error": "Pending files JSON missing.", "details": str(files_path)}
        obj = json.loads(files_path.read_text(encoding="utf-8"))
        files = obj.get("files") or []
        if not isinstance(files, list):
            return {"error": "Pending files JSON is invalid (files must be a list)."}

        ok_apply, notes = _apply_files_to_dir(_repo_root(), files)
        apply_log = _write_run_log("apply_apply", notes)
        if not ok_apply:
            return {
                "error": "Failed to apply file edits to real repo.",
                "details": notes,
                "apply_log": apply_log,
                "backup": str(backup_root.relative_to(_repo_root())).replace("\\", "/"),
            }
    else:
        ok_apply, apply_out = _apply_patch_with_git_apply(_repo_root(), diff_path)
        apply_log = _write_run_log("apply_apply", apply_out)
        if not ok_apply:
            return {
                "error": "Failed to apply diff to real repo.",
                "details": apply_out,
                "apply_log": apply_log,
                "backup": str(backup_root.relative_to(_repo_root())).replace("\\", "/"),
            }

    # Re-check
    ok_check, check_out = _run_checks(_repo_root())
    check_log = _write_run_log("apply_checks", check_out)

    state["last_test"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok_check,
        "check": "compileall+smoke",
        "log": check_log,
        "target": "repo",
    }
    state["pending_patch"] = None
    _save_state(state)

    return {
        "result": {
            "applied": True,
            "backup": str(backup_root.relative_to(_repo_root())).replace("\\", "/"),
            "apply_log": apply_log,
            "checks_ok": ok_check,
            "checks_log": check_log,
            # Backward compat (optional):
            "compileall_ok": ok_check,
            "compileall_log": check_log,
        }
    }
