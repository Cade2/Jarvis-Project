from __future__ import annotations
from typing import Any, Dict
import platform
import psutil
import time

def system_get_info(params: Dict[str, Any]) -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "result": {
            "os": platform.platform(),
            "cpu": platform.processor() or platform.machine(),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_available_gb": round(vm.available / (1024**3), 2),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
        }
    }

def system_get_storage(params: Dict[str, Any]) -> Dict[str, Any]:
    drives = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        drives.append({
            "mount": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
        })
    return {"result": {"drives": drives}}
