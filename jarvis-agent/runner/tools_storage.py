from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
from pathlib import Path
import os
import time
import heapq
import psutil


def _bytes_to_gb(n: int) -> float:
    return round(n / (1024**3), 2)


def _get_default_drive_mount() -> str:
    # Prefer system drive on Windows
    sys_drive = os.environ.get("SystemDrive", "C:")
    if not sys_drive.endswith("\\"):
        sys_drive += "\\"
    return sys_drive


def _resolve_drive_mount(params: Dict[str, Any]) -> str:
    """
    Accepts:
      - {"drive": "C"} or {"drive": "C:"} or {"drive": "C:\\"}
      - {"mount": "D:\\"}
    """
    drive = (params.get("drive") or "").strip()
    mount = (params.get("mount") or "").strip()

    if mount:
        if not mount.endswith("\\"):
            mount += "\\"
        return mount

    if drive:
        d = drive.upper().replace("/", "\\")
        if len(d) == 1:
            d = d + ":"
        if not d.endswith("\\"):
            d = d + "\\"
        return d

    return _get_default_drive_mount()


def _dir_size_fast(root: Path, *, max_entries: int, max_depth: int, deadline: float) -> Tuple[int, int, bool]:
    """
    Fast best-effort directory size with limits.
    Returns: (bytes, entries_scanned, partial)
    """
    total = 0
    scanned = 0
    partial = False

    stack: List[Tuple[Path, int]] = [(root, 0)]
    while stack:
        if time.time() > deadline or scanned >= max_entries:
            partial = True
            break

        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    if time.time() > deadline or scanned >= max_entries:
                        partial = True
                        break

                    scanned += 1
                    try:
                        if entry.is_symlink():
                            continue

                        if entry.is_file(follow_symlinks=False):
                            try:
                                total += entry.stat(follow_symlinks=False).st_size
                            except Exception:
                                pass
                        elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                            stack.append((Path(entry.path), depth + 1))
                    except Exception:
                        continue
        except Exception:
            continue

    return total, scanned, partial


def _largest_files(root: Path, *, top_n: int, min_bytes: int, max_entries: int, max_depth: int, deadline: float) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Return top N largest files under root (best effort).
    """
    heap: List[Tuple[int, str]] = []
    partial = False
    scanned = 0

    stack: List[Tuple[Path, int]] = [(root, 0)]
    while stack:
        if time.time() > deadline or scanned >= max_entries:
            partial = True
            break

        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    if time.time() > deadline or scanned >= max_entries:
                        partial = True
                        break

                    scanned += 1
                    try:
                        if entry.is_symlink():
                            continue

                        if entry.is_file(follow_symlinks=False):
                            try:
                                sz = entry.stat(follow_symlinks=False).st_size
                            except Exception:
                                continue

                            if sz < min_bytes:
                                continue

                            if len(heap) < top_n:
                                heapq.heappush(heap, (sz, entry.path))
                            else:
                                if sz > heap[0][0]:
                                    heapq.heapreplace(heap, (sz, entry.path))

                        elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                            stack.append((Path(entry.path), depth + 1))

                    except Exception:
                        continue
        except Exception:
            continue

    heap.sort(reverse=True)
    results = [{"path": p, "size_gb": _bytes_to_gb(sz)} for sz, p in heap]
    return results, partial


def storage_get_categories(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Storage category breakdown (best-effort estimate).
    """
    params = params or {}

    mount = _resolve_drive_mount(params)
    drive_root = Path(mount)

    # Limits to avoid hanging
    deadline_seconds = float(params.get("deadline_seconds", 8.0))
    max_entries = int(params.get("max_entries", 120000))
    max_depth = int(params.get("max_depth", 8))
    deadline = time.time() + deadline_seconds

    # Drive usage
    usage = psutil.disk_usage(mount)
    total_b = int(usage.total)
    free_b = int(usage.free)
    used_b = total_b - free_b

    user = Path(os.environ.get("USERPROFILE", str(Path.home())))
    categories: List[Dict[str, Any]] = []
    notes: List[str] = []
    any_partial = False

    def add_category(name: str, path: Optional[Path]):
        nonlocal any_partial
        if not path:
            return
        if not path.exists():
            return
        b, scanned, partial = _dir_size_fast(
            path, max_entries=max_entries, max_depth=max_depth, deadline=deadline
        )
        any_partial = any_partial or partial
        categories.append({
            "name": name,
            "path": str(path),
            "size_gb": _bytes_to_gb(b),
            "partial": partial,
            "scanned_entries": scanned,
        })

    # User folders
    add_category("Downloads", user / "Downloads")
    add_category("Desktop", user / "Desktop")
    add_category("Documents", user / "Documents")
    add_category("Pictures", user / "Pictures")
    add_category("Videos", user / "Videos")
    add_category("Music", user / "Music")

    # Temp
    temp1 = Path(os.environ.get("TEMP", ""))
    temp2 = Path(os.environ.get("TMP", ""))
    win_temp = drive_root / "Windows" / "Temp"
    add_category("Temporary files (User TEMP)", temp1 if str(temp1) else None)
    if temp2 != temp1:
        add_category("Temporary files (TMP)", temp2 if str(temp2) else None)
    add_category("Temporary files (Windows Temp)", win_temp)

    # Recycle Bin (best effort)
    add_category("Recycle Bin", drive_root / "$Recycle.Bin")

    # Installed apps (estimate)
    add_category("Installed apps (Program Files)", drive_root / "Program Files")
    add_category("Installed apps (Program Files x86)", drive_root / "Program Files (x86)")
    add_category("Installed apps (ProgramData)", drive_root / "ProgramData")
    add_category("Installed apps (User Local Programs)", user / "AppData" / "Local" / "Programs")

    # OneDrive (optional)
    onedrive = user / "OneDrive"
    if onedrive.exists():
        add_category("OneDrive (local)", onedrive)

    # Calculate "Other"
    known_used_b = 0
    for c in categories:
        # approximate: gb -> bytes
        known_used_b += int((c["size_gb"] * (1024**3)))

    other_b = max(0, used_b - known_used_b)
    categories.append({
        "name": "Other",
        "path": str(drive_root),
        "size_gb": _bytes_to_gb(other_b),
        "partial": True,
        "scanned_entries": None,
    })
    any_partial = True

    if any_partial:
        notes.append("Some categories are estimates (scan limits/time limits). Increase deadline_seconds/max_entries for deeper scans.")

    return {
        "result": {
            "mount": mount,
            "total_gb": _bytes_to_gb(total_b),
            "used_gb": _bytes_to_gb(used_b),
            "free_gb": _bytes_to_gb(free_b),
            "used_percent": round((used_b / total_b) * 100, 2) if total_b else None,
            "categories": sorted(categories, key=lambda x: x.get("size_gb", 0), reverse=True),
            "notes": notes,
        }
    }


def storage_cleanup_recommendations(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleanup recommendations (read-only).
    """
    params = params or {}
    mount = _resolve_drive_mount(params)

    # tighter limits for cleanup scan
    deadline_seconds = float(params.get("deadline_seconds", 6.0))
    deadline = time.time() + deadline_seconds

    user = Path(os.environ.get("USERPROFILE", str(Path.home())))
    drive_root = Path(mount)

    # Estimate key cleanup targets
    targets = []

    # Downloads size + biggest files
    downloads = user / "Downloads"
    dl_size, _, dl_partial = _dir_size_fast(downloads, max_entries=30000, max_depth=8, deadline=deadline) if downloads.exists() else (0, 0, False)
    big_files, big_partial = _largest_files(downloads, top_n=10, min_bytes=500 * 1024 * 1024, max_entries=25000, max_depth=8, deadline=deadline) if downloads.exists() else ([], False)

    # Recycle bin
    recycle = drive_root / "$Recycle.Bin"
    rb_size, _, rb_partial = _dir_size_fast(recycle, max_entries=20000, max_depth=6, deadline=deadline) if recycle.exists() else (0, 0, False)

    # Temp
    temp1 = Path(os.environ.get("TEMP", ""))
    win_temp = drive_root / "Windows" / "Temp"
    t1_size, _, t1_partial = _dir_size_fast(temp1, max_entries=20000, max_depth=6, deadline=deadline) if str(temp1) and temp1.exists() else (0, 0, False)
    wt_size, _, wt_partial = _dir_size_fast(win_temp, max_entries=20000, max_depth=6, deadline=deadline) if win_temp.exists() else (0, 0, False)

    targets.append({"name": "Downloads", "estimated_gb": _bytes_to_gb(dl_size), "partial": dl_partial})
    targets.append({"name": "Recycle Bin", "estimated_gb": _bytes_to_gb(rb_size), "partial": rb_partial})
    targets.append({"name": "User TEMP", "estimated_gb": _bytes_to_gb(t1_size), "partial": t1_partial})
    targets.append({"name": "Windows Temp", "estimated_gb": _bytes_to_gb(wt_size), "partial": wt_partial})

    # Build recommendations
    recommendations = []
    if dl_size > 0:
        recommendations.append({
            "title": "Review Downloads folder",
            "estimated_free_gb": _bytes_to_gb(dl_size),
            "note": "This is often the biggest safe cleanup target. Consider deleting old installers/ISOs/archives.",
        })
    if rb_size > 0:
        recommendations.append({
            "title": "Empty Recycle Bin",
            "estimated_free_gb": _bytes_to_gb(rb_size),
            "note": "Files are not permanently removed until the Recycle Bin is emptied.",
        })
    if t1_size + wt_size > 0:
        recommendations.append({
            "title": "Clear temporary files",
            "estimated_free_gb": _bytes_to_gb(t1_size + wt_size),
            "note": "Temp files usually safe, but some may be in-use. We are NOT deleting anything in MK2 storage yet.",
        })

    return {
        "result": {
            "mount": mount,
            "targets": sorted(targets, key=lambda x: x["estimated_gb"], reverse=True),
            "largest_downloads_files_over_500mb": big_files,
            "partial": (dl_partial or rb_partial or t1_partial or wt_partial or big_partial),
            "recommendations": recommendations,
            "note": "MK2: Read-only recommendations only. No deletions are performed.",
        }
    }
