from __future__ import annotations
from typing import Any, Dict, Tuple
import os
import json
import subprocess


def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def gaming_get_game_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read Windows Game Mode setting (best-effort).
    Uses HKCU:\Software\Microsoft\GameBar values.
    """
    if os.name != "nt":
        return {"error": "gaming.get_game_mode is only implemented on Windows right now."}

    ps = r"""
try {
  $path = "HKCU:\Software\Microsoft\GameBar"
  $p = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue

  $auto = $null
  $allow = $null
  if ($p) {
    $auto = $p.AutoGameModeEnabled
    $allow = $p.AllowAutoGameMode
  }

  # Determine enabled (prefer AutoGameModeEnabled if present)
  $enabled = $false
  if ($auto -ne $null) { $enabled = [bool]($auto -eq 1) }
  elseif ($allow -ne $null) { $enabled = [bool]($allow -eq 1) }

  @{
    supported = $true
    enabled = $enabled
    auto_value = $auto
    allow_value = $allow
    source = "HKCU:\Software\Microsoft\GameBar"
  } | ConvertTo-Json -Depth 6 -Compress
} catch {
  @{ supported = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 6 -Compress
}
"""
    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "error": err or "No output"}}
    return {"result": json.loads(out)}


def gaming_set_game_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Toggle Game Mode on/off.
    Writes:
      HKCU:\Software\Microsoft\GameBar\AutoGameModeEnabled (DWORD)
      HKCU:\Software\Microsoft\GameBar\AllowAutoGameMode (DWORD)
    """
    if os.name != "nt":
        return {"error": "gaming.set_game_mode is only implemented on Windows right now."}

    enabled = bool(params.get("enabled", True))
    v = 1 if enabled else 0

    requested_ps_bool = "$true" if enabled else "$false"

    ps = rf"""
    try {{
    $path = "HKCU:\Software\Microsoft\GameBar"
    New-Item -Path $path -Force | Out-Null

    # Set both keys (Windows may use one or the other depending on build)
    Set-ItemProperty -Path $path -Name "AutoGameModeEnabled" -Type DWord -Value {v} -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $path -Name "AllowAutoGameMode" -Type DWord -Value {v} -Force -ErrorAction SilentlyContinue

    $p = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue
    $auto = $null
    $allow = $null
    if ($p) {{
        $auto = $p.AutoGameModeEnabled
        $allow = $p.AllowAutoGameMode
    }}

    $enabled_now = $false
    if ($auto -ne $null) {{ $enabled_now = [bool]($auto -eq 1) }}
    elseif ($allow -ne $null) {{ $enabled_now = [bool]($allow -eq 1) }}

    @{{
        supported = $true
        requested_enabled = {requested_ps_bool}
        enabled = $enabled_now
        auto_value = $auto
        allow_value = $allow
        source = "HKCU:\Software\Microsoft\GameBar"
        note = "If Settings UI doesn't update immediately, reopen Settings."
    }} | ConvertTo-Json -Depth 6 -Compress
    }} catch {{
    @{{ supported = $false; error = $_.Exception.Message }} | ConvertTo-Json -Depth 6 -Compress
    }}
    """

    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "error": err or "No output"}}
    return {"result": json.loads(out)}
