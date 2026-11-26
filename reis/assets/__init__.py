"""Paketlenmiş veri dosyaları (hologram modeli vb.)."""
from __future__ import annotations

import os
import sys


def asset_path(*parts: str) -> str:
    """Kaynaktan çalışırken paket klasörü, PyInstaller ile donmuşken _MEIPASS/reis/assets altında."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = os.path.join(sys._MEIPASS, "reis", "assets")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)
