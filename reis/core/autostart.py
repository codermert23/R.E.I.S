"""Windows açılışında başlatma: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (admin gerekmez)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Reis"


def _pythonw(executable: str) -> str:
    p = Path(executable)
    if p.name.lower() == "python.exe":
        w = p.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(p)


def build_command(frozen: bool | None = None, executable: str | None = None) -> str:
    """Kaynaktan: `"pythonw.exe" -m reis`; paketlenmiş: `"Reis.exe"`."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exe = executable or sys.executable
    if frozen:
        return f'"{exe}"'
    return f'"{_pythonw(exe)}" -m reis'


def _wr(winreg_mod=None):
    if winreg_mod is not None:
        return winreg_mod
    import winreg

    return winreg


def is_enabled(winreg_mod=None, key_path: str = RUN_KEY) -> bool:
    wr = _wr(winreg_mod)
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, key_path, 0, wr.KEY_READ) as k:
            wr.QueryValueEx(k, VALUE_NAME)
            return True
    except OSError:
        return False


def current_command(winreg_mod=None, key_path: str = RUN_KEY) -> str | None:
    wr = _wr(winreg_mod)
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, key_path, 0, wr.KEY_READ) as k:
            return wr.QueryValueEx(k, VALUE_NAME)[0]
    except OSError:
        return None


def enable(command: str | None = None, winreg_mod=None, key_path: str = RUN_KEY) -> None:
    wr = _wr(winreg_mod)
    with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, key_path, 0, wr.KEY_SET_VALUE) as k:
        wr.SetValueEx(k, VALUE_NAME, 0, wr.REG_SZ, command or build_command())


def disable(winreg_mod=None, key_path: str = RUN_KEY) -> None:
    wr = _wr(winreg_mod)
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, key_path, 0, wr.KEY_SET_VALUE) as k:
            wr.DeleteValue(k, VALUE_NAME)
    except FileNotFoundError:
        pass  # zaten yok


def set_enabled(on: bool, **kw) -> None:
    enable(**kw) if on else disable(**{k: v for k, v in kw.items() if k != "command"})
