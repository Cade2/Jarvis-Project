# agent/devtools.py
from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
from datetime import datetime
import json
import shutil
import subprocess
import os

# NOTE:
# - This module is "agent-side" (runner-independent).
# - It only writes inside workspace/, except dev.apply_patch which applies to real repo (CRITICAL).


# -------------------------
# Paths + state helpers
# -------------------------

import subprocess
from datetime import datetime
from pathlib import Path

def _run(cmd, cwd: Path):
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        shell=False
    )
    return p.returncode, p.stdout, p.stderr

def _ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo_root() -> Path:
    # agent/devtools.py -> agent/ -> repo root
    return Path(__file__).resolve().parent.parent


def _workspace_root() -> Path:
    return _repo_root() / "workspace"


def _sandbox_root() -> Path:
    return _workspace_root() / "repo_sandbox"


def _patches_dir() -> Path:
    return _workspace_root() / "patches"


def _runs_dir() -> Path:
    return _workspace_root() / "runs"


def _state_path() -> Path:
    return _workspace_root() / "state.json"


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
        # If corrupted, reset safely
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
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
        return True
    except Exception:
        return False


def _copy_repo_to_sandbox() -> None:
    """
    Recreate workspace/repo_sandbox as a clean copy of repo (excluding workspace/, logs/, .git, caches).
    """
    root = _repo_root()
    sandbox = _sandbox_root()

    if sandbox.exists():
        shutil.rmtree(sandbox)

    def _ignore(dirpath: str, names: List[str]) -> set:
        # dirpath is str
        ignore = set()
        base = os.path.basename(dirpath).lower()

        # Always ignore VCS + caches
        for n in names:
            nl = n.lower()
            if nl in {".git", ".idea", ".vscode"}:
                ignore.add(n)
            if nl == "__pycache__":
                ignore.add(n)

        # Ignore root-level folders we don't want duplicated
        # (workspace must not nest inside sandbox)
        if Path(dirpath).resolve() == root.resolve():
            for n in names:
                nl = n.lower()
                if nl in {"workspace", "logs"}:
                    ignore.add(n)

        return ignore

    shutil.copytree(root, sandbox, ignore=_ignore)


def _run_compileall(cwd: Path) -> Tuple[bool, str]:
    """
    Run basic deterministic checks. Start with compileall.
    """
    cmd = ["python", "-m", "compileall", "."]
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    ok = (p.returncode == 0)
    return ok, out.strip()


def _write_run_log(prefix: str, content: str) -> str:
    run_id = _now_id()
    p = _runs_dir() / f"{prefix}_{run_id}.log"
    p.write_text(content, encoding="utf-8", errors="replace")
    return str(p.relative_to(_repo_root()))


def _apply_patch_with_git_apply(base_dir: Path, diff_path: Path) -> Tuple[bool, str]:
    """
    Apply a unified diff to base_dir using git apply (works even without .git).
    """
    if not _git_available():
        return False, "git not found. Install Git for Windows, or ensure `git` is in PATH."

    # --whitespace=nowarn to reduce noise
    cmd = ["git", "apply", "--whitespace=nowarn", str(diff_path)]
    p = subprocess.run(cmd, cwd=str(base_dir), capture_output=True, text=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return (p.returncode == 0), out.strip()


def _backup_changed_files(repo_dir: Path, diff_text: str, backup_root: Path) -> None:
    """
    Very simple backup: parse '+++ b/<path>' lines from diff and copy those files (if they exist).
    """
    backup_root.mkdir(parents=True, exist_ok=True)
    changed = set()

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            rel = line[len("+++ b/"):].strip()
            # Skip /dev/null for deletes
            if rel and rel != "/dev/null":
                changed.add(rel)

    for rel in changed:
        src = (repo_dir / rel).resolve()
        if src.exists() and src.is_file():
            dst = (backup_root / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


# -------------------------
# Public tool functions
# -------------------------

def dev_status(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Shows current dev pipeline state:
    - pending patch (if any)
    - last test result
    - sandbox path
    """
    _ensure_workspace_layout()
    state = _load_state()

    sandbox_exists = _sandbox_root().exists()
    pending = state.get("pending_patch")
    last_test = state.get("last_test")

    return {
        "result": {
            "sandbox_exists": sandbox_exists,
            "sandbox_path": str(_sandbox_root().relative_to(_repo_root())),
            "pending_patch": pending,
            "last_test": last_test,
        }
    }


def dev_sandbox_reset(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Re-copy repo -> workspace/repo_sandbox.
    Clears pending patch state.
    """
    _ensure_workspace_layout()
    _copy_repo_to_sandbox()

    ok, out = _run_compileall(_sandbox_root())
    run_log = _write_run_log("sandbox_reset_compileall", out)

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


def dev_propose_patch(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Save diff to workspace/patches/, apply to sandbox only, run compileall,
    and store as pending patch in workspace/state.json.
    """
    params = params or {}
    diff_text = params.get("diff", "")
    desc = params.get("description", "")

    if not diff_text.strip():
        return {"error": "Missing 'diff' content."}

    _ensure_workspace_layout()

    sandbox = _sandbox_root()
    if not sandbox.exists():
        return {"error": "Sandbox does not exist. Run 'sandbox reset' first."}

    # Save patch file
    patch_id = _now_id()
    patch_rel = Path("workspace") / "patches" / f"pending_{patch_id}.diff"
    patch_path = (_repo_root() / patch_rel).resolve()
    # Ensure newline at end (git apply can be picky)
    if not diff_text.endswith("\n"):
        diff_text += "\n"
    _write_text(patch_path, diff_text)

    # 1) Check patch
    rc, out, err = _run(["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)], cwd=sandbox)
    if rc != 0:
        return {
            "error": "Patch check failed (git apply --check).",
            "details": (err or out).strip() or "No output returned.",
        }

    # 2) Apply patch to SANDBOX only
    rc, out, err = _run(["git", "apply", "--whitespace=nowarn", str(patch_path)], cwd=sandbox)
    if rc != 0:
        return {
            "error": "Failed to apply patch to sandbox (git apply).",
            "details": (err or out).strip() or "No output returned.",
        }

    # 3) Compile check in sandbox
    ok, compile_out = _run_compileall(sandbox)
    run_log = _write_run_log(f"propose_patch_compileall_{patch_id}", compile_out)

    # 4) Persist pending patch in state.json
    state = _load_state()
    state["pending_patch"] = {
        "id": patch_id,
        "diff_path": str(patch_rel).replace("/", "\\"),
        "description": desc,
        "when": datetime.now().isoformat(timespec="seconds"),
        "sandbox_compileall_ok": ok,
        "compile_log": run_log,
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
            "run_log": run_log,
        }
    }




def dev_discard_patch(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Discard pending patch and reset sandbox to clean.
    """
    _ensure_workspace_layout()
    if _sandbox_root().exists():
        shutil.rmtree(_sandbox_root())
    _copy_repo_to_sandbox()

    state = _load_state()
    state["pending_patch"] = None
    _save_state(state)

    return {"result": {"discarded": True, "sandbox_reset": True}}


def dev_apply_patch(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Apply the current pending patch to the REAL repo (CRITICAL).
    - Backs up files mentioned in diff to workspace/patches/backups/<patch_id>/
    - Applies diff with git apply to repo root
    - Runs checks (compileall)
    """
    _ensure_workspace_layout()
    state = _load_state()
    pending = state.get("pending_patch")
    if not pending:
        return {"error": "No pending patch to apply. Use dev.propose_patch first."}

    diff_rel = pending.get("diff_path")
    if not diff_rel:
        return {"error": "Pending patch missing diff_path."}

    diff_path = (_repo_root() / diff_rel).resolve()
    if not diff_path.exists():
        return {"error": f"Diff file not found: {diff_path}"}

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")

    # Backup
    patch_id = pending.get("id", _now_id())
    backup_root = _patches_dir() / "backups" / patch_id
    _backup_changed_files(_repo_root(), diff_text, backup_root)

    # Apply to real repo
    ok_apply, apply_out = _apply_patch_with_git_apply(_repo_root(), diff_path)
    apply_log = _write_run_log("apply_apply", apply_out)

    if not ok_apply:
        return {
            "error": "Failed to apply diff to real repo.",
            "details": apply_out,
            "apply_log": apply_log,
            "backup": str(backup_root.relative_to(_repo_root())),
        }

    # Run checks in real repo
    ok_check, check_out = _run_compileall(_repo_root())
    check_log = _write_run_log("apply_compileall", check_out)

    state["last_test"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok_check,
        "check": "compileall",
        "log": check_log,
        "target": "repo",
    }
    state["pending_patch"] = None
    _save_state(state)

    return {
        "result": {
            "applied": True,
            "backup": str(backup_root.relative_to(_repo_root())),
            "apply_log": apply_log,
            "compileall_ok": ok_check,
            "compileall_log": check_log,
        }
    }
