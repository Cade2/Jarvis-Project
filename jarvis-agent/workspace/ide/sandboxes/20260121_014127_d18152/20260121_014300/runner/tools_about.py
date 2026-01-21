# runner/tools_about.py
from __future__ import annotations
from typing import Any, Dict
import os
import re
import json
import subprocess

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,13}[A-Za-z0-9])?$")  # 1-15 chars, no leading/trailing '-'

def _run_powershell_json(ps_script: str) -> Dict[str, Any]:
    """Runs a PowerShell script that prints JSON to stdout."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=25,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "PowerShell command failed.")
    out = (proc.stdout or "").strip()
    return json.loads(out) if out else {}

def about_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    ps = r"""
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1

$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$install = $os.InstallDate

[pscustomobject]@{
  device_name        = $env:COMPUTERNAME
  manufacturer       = $cs.Manufacturer
  model              = $cs.Model
  system_type        = $cs.SystemType
  cpu                = $cpu.Name
  cpu_max_mhz        = $cpu.MaxClockSpeed
  ram_total_gb       = [math]::Round($cs.TotalPhysicalMemory/1GB, 2)
  gpu                = $gpu.Name
  windows_product    = $cv.ProductName
  windows_edition_id = $cv.EditionID
  display_version    = $cv.DisplayVersion
  os_version         = $os.Version
  os_build           = $os.BuildNumber
  installed_on       = $install.ToString("yyyy-MM-dd")
} | ConvertTo-Json -Compress
"""
    data = _run_powershell_json(ps)
    return {
        "result": {
            "supported": True,
            **data,
            "note": "Rename requires admin and a restart to fully apply.",
        }
    }

def about_rename_pc(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name'")

    if not _HOSTNAME_RE.match(name):
        raise ValueError("Invalid PC name. Use 1–15 characters: letters/numbers/hyphen, no leading/trailing hyphen.")

    ps = rf"""
Rename-Computer -NewName "{name}" -Force -PassThru | ConvertTo-Json -Compress
"""
    data = _run_powershell_json(ps)

    return {
        "result": {
            "supported": True,
            "changed": True,
            "new_name": name,
            "rename_result": data,
            "restart_required": True,
            "note": "Restart Windows to fully apply the new PC name.",
        }
    }
