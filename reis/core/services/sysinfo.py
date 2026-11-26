"""CPU / RAM / disk anlık görüntüsü (psutil)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class SysSnapshot:
    cpu: float
    ram: float
    disk: float
    disk_used_gb: float
    disk_total_gb: float


def snapshot(path: str | None = None) -> SysSnapshot:
    import psutil

    path = path or os.path.abspath(os.sep)
    du = psutil.disk_usage(path)
    return SysSnapshot(
        cpu=float(psutil.cpu_percent(interval=None)),
        ram=float(psutil.virtual_memory().percent),
        disk=float(du.percent),
        disk_used_gb=du.used / 1024 ** 3,
        disk_total_gb=du.total / 1024 ** 3,
    )
