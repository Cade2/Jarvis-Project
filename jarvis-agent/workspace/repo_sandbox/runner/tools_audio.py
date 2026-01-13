from __future__ import annotations
from typing import Any, Dict, Tuple
import os
import json
import subprocess
import time

def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

_PS_COREAUDIO = r"""
Add-Type -ErrorAction SilentlyContinue -Language CSharp -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public enum EDataFlow { eRender = 0, eCapture = 1, eAll = 2 }
public enum ERole { eConsole = 0, eMultimedia = 1, eCommunications = 2 }

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class MMDeviceEnumeratorComObject { }

[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    int NotImpl1();
    int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice ppDevice);
}

[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams,
        [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
}

[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
    int RegisterControlChangeNotify(IntPtr pNotify);
    int UnregisterControlChangeNotify(IntPtr pNotify);
    int GetChannelCount(out uint pnChannelCount);
    int SetMasterVolumeLevel(float fLevelDB, Guid pguidEventContext);
    int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
    int GetMasterVolumeLevel(out float pfLevelDB);
    int GetMasterVolumeLevelScalar(out float pfLevel);
    int SetChannelVolumeLevel(uint nChannel, float fLevelDB, Guid pguidEventContext);
    int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, Guid pguidEventContext);
    int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
    int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
    int GetMute(out bool pbMute);
    int GetVolumeStepInfo(out uint pnStep, out uint pnStepCount);
    int VolumeStepUp(Guid pguidEventContext);
    int VolumeStepDown(Guid pguidEventContext);
    int QueryHardwareSupport(out uint pdwHardwareSupportMask);
    int GetVolumeRange(out float pflVolumeMindB, out float pflVolumeMaxdB, out float pflVolumeIncrementdB);
}

public static class CoreAudio {
    private const int CLSCTX_ALL = 23;

    private static IAudioEndpointVolume GetEndpoint() {
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice device;
        int hr = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out device);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);

        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object obj;
        hr = device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out obj);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);

        return (IAudioEndpointVolume)obj;
    }

    public static float GetVolumeScalar() {
        var ep = GetEndpoint();
        float v;
        int hr = ep.GetMasterVolumeLevelScalar(out v);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);
        return v;
    }

    public static void SetVolumeScalar(float v) {
        if (v < 0) v = 0;
        if (v > 1) v = 1;
        var ep = GetEndpoint();
        int hr = ep.SetMasterVolumeLevelScalar(v, Guid.Empty);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);
    }

    public static bool GetMute() {
        var ep = GetEndpoint();
        bool m;
        int hr = ep.GetMute(out m);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);
        return m;
    }

    public static void SetMute(bool m) {
        var ep = GetEndpoint();
        int hr = ep.SetMute(m, Guid.Empty);
        if (hr != 0) Marshal.ThrowExceptionForHR(hr);
    }
}
"@ | Out-Null
"""

def audio_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "audio.get_state is only implemented on Windows right now."}

    ps = _PS_COREAUDIO + r"""
try {
  $v = [CoreAudio]::GetVolumeScalar()
  $m = [CoreAudio]::GetMute()
  @{ supported = $true; volume = [int]([math]::Round($v * 100)); muted = [bool]$m } | ConvertTo-Json -Depth 4
} catch {
  @{ supported = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 6
}
"""
    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "error": err or "No output"}}
    return {"result": json.loads(out)}

def audio_set_volume(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "audio.set_volume is only implemented on Windows right now."}

    level = params.get("level")
    if level is None:
        return {"error": "Missing param 'level' (0-100)."}

    try:
        level_int = int(level)
    except Exception:
        return {"error": "Param 'level' must be an integer 0-100."}

    level_int = max(0, min(100, level_int))
    scalar = level_int / 100.0

    before = audio_get_state({}).get("result", {})
    if not before.get("supported"):
        return {"result": {"supported": False, "requested_level": level_int, "before": before}}

    ps = _PS_COREAUDIO + rf"""
try {{
  [CoreAudio]::SetVolumeScalar({scalar})
  Start-Sleep -Milliseconds 150
  $v = [CoreAudio]::GetVolumeScalar()
  $m = [CoreAudio]::GetMute()
  @{{
    supported = $true
    ok = $true
    volume = [int]([math]::Round($v * 100))
    muted = [bool]$m
  }} | ConvertTo-Json -Depth 4
}} catch {{
  @{{
    supported = $false
    ok = $false
    error = $_.Exception.Message
  }} | ConvertTo-Json -Depth 6
}}
"""
    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "requested_level": level_int, "error": err or "No output"}}
    after = json.loads(out)

    return {
        "result": {
            "supported": bool(after.get("supported")),
            "requested_level": level_int,
            "before": before,
            "after": after,
            "changed": (before.get("volume") != after.get("volume")) if after.get("supported") else False,
        }
    }

def audio_set_mute(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "audio.set_mute is only implemented on Windows right now."}

    muted = params.get("muted")
    if muted is None:
        return {"error": "Missing param 'muted' (true/false)."}
    muted = bool(muted)

    before = audio_get_state({}).get("result", {})
    if not before.get("supported"):
        return {"result": {"supported": False, "requested_muted": muted, "before": before}}

    ps_bool = "$true" if muted else "$false"

    ps = _PS_COREAUDIO + rf"""
try {{
  [CoreAudio]::SetMute({ps_bool})
  Start-Sleep -Milliseconds 150
  $v = [CoreAudio]::GetVolumeScalar()
  $m = [CoreAudio]::GetMute()
  @{{
    supported = $true
    ok = $true
    volume = [int]([math]::Round($v * 100))
    muted = [bool]$m
  }} | ConvertTo-Json -Depth 4
}} catch {{
  @{{
    supported = $false
    ok = $false
    error = $_.Exception.Message
  }} | ConvertTo-Json -Depth 6
}}
"""
    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "requested_muted": muted, "error": err or "No output"}}

    try:
        after = json.loads(out)
    except Exception:
        return {"result": {"supported": False, "requested_muted": muted, "error": "JSON parse failed", "raw": out, "stderr": err}}

    return {
        "result": {
            "supported": bool(after.get("supported")),
            "requested_muted": muted,
            "before": before,
            "after": after,
            "changed": (before.get("muted") != after.get("muted")) if after.get("supported") else False,
        }
    }
