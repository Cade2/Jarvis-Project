from __future__ import annotations
from typing import Any, Dict, List
import os
import subprocess

def _list_installed_via_registry() -> List[Dict[str, Any]]:
    if os.name != "nt":
        return []
    import winreg

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    apps: List[Dict[str, Any]] = []
    for root, path in roots:
        try:
            k = winreg.OpenKey(root, path)
        except Exception:
            continue

        for i in range(0, 5000):
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            try:
                sk = winreg.OpenKey(k, sub)
                name, _ = winreg.QueryValueEx(sk, "DisplayName")
                version = publisher = install_loc = None

                try: version, _ = winreg.QueryValueEx(sk, "DisplayVersion")
                except Exception: pass
                try: publisher, _ = winreg.QueryValueEx(sk, "Publisher")
                except Exception: pass
                try: install_loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                except Exception: pass

                apps.append({
                    "name": name,
                    "version": version,
                    "publisher": publisher,
                    "install_location": install_loc,
                })
            except Exception:
                continue

    seen = set()
    dedup = []
    for a in apps:
        n = a.get("name")
        if not n or n in seen:
            continue
        seen.add(n)
        dedup.append(a)

    return sorted(dedup, key=lambda x: x["name"].lower())

def apps_list_installed(params: Dict[str, Any]) -> Dict[str, Any]:
    apps = _list_installed_via_registry()
    return {"result": {"apps": apps[:500]}}

def apps_open(params: Dict[str, Any]) -> Dict[str, Any]:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name'")

    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        return {"result": {"launched": True, "method": "start"}}

    subprocess.Popen([name])
    return {"result": {"launched": True, "method": "exec"}}

def apps_close(params: Dict[str, Any]) -> Dict[str, Any]:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name'")

    if os.name == "nt":
        img = name if name.lower().endswith(".exe") else f"{name}.exe"
        p = subprocess.run(["taskkill", "/IM", img, "/F"], capture_output=True, text=True)
        return {"result": {"closed": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr}}

    return {"result": {"closed": False, "error": "Unsupported OS"}}
