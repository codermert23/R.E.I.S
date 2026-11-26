"""Global kısayol: Win32 RegisterHotKey + QAbstractNativeEventFilter (WM_HOTKEY)."""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5E15

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "windows": MOD_WIN, "meta": MOD_WIN}
_NAMED = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
          "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
          "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
          "pause": 0x13, "printscreen": 0x2C, "scrolllock": 0x91, "capslock": 0x14}


class HotkeyError(ValueError):
    pass


def parse_hotkey(text: str) -> tuple[int, int]:
    """'F1' -> (0, 0x70); 'Ctrl+Alt+R' -> (MOD_CONTROL|MOD_ALT, ord('R')). Modifier'lar MOD_NOREPEAT içermez."""
    parts = [p.strip().lower() for p in (text or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("Boş kısayol")
    mods = 0
    for m in parts[:-1]:
        if m not in _MODS:
            raise HotkeyError(f"Bilinmeyen değiştirici tuş: {m}")
        mods |= _MODS[m]
    key = parts[-1]
    if key in _MODS:
        raise HotkeyError("Kısayolda asıl bir tuş olmalı")
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isalnum() and key.isascii():
        vk = ord(key.upper())
    elif key in _NAMED:
        vk = _NAMED[key]
    else:
        raise HotkeyError(f"Bilinmeyen tuş: {key}")
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, callback, hotkey_id: int = HOTKEY_ID):
        super().__init__()
        self._cb = callback
        self._id = hotkey_id

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG" or event_type == "windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                return False, 0
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                self._cb()
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    pressed = Signal()
    warning = Signal(str)

    DEBOUNCE_S = 0.25

    def __init__(self, parent=None, user32=None, hotkey_id: int = HOTKEY_ID):
        super().__init__(parent)
        self._user32 = user32
        self._id = hotkey_id
        self._registered = False
        self._filter = _Filter(self._on_hotkey, hotkey_id)
        self._last = 0.0
        self._installed = False
        self.current = ""

    def _u32(self):
        if self._user32 is None:
            self._user32 = ctypes.windll.user32
        return self._user32

    def install(self, app) -> None:
        if not self._installed:
            app.installNativeEventFilter(self._filter)
            self._installed = True

    def _on_hotkey(self):
        now = time.monotonic()
        if now - self._last < self.DEBOUNCE_S:
            return
        self._last = now
        self.pressed.emit()

    def register(self, text: str) -> bool:
        """Kaydeder; başarısızsa `warning` yayar ve False döner. Önceki kayıt kaldırılır."""
        self.unregister()
        if sys.platform != "win32" and self._user32 is None:
            self.warning.emit("Global kısayol yalnızca Windows'ta desteklenir.")
            return False
        try:
            mods, vk = parse_hotkey(text)
        except HotkeyError as e:
            self.warning.emit(f"Kısayol geçersiz ({text}): {e}")
            return False
        ok = bool(self._u32().RegisterHotKey(None, self._id, mods | MOD_NOREPEAT, vk))
        if not ok:
            self.warning.emit(f"{text} kısayolu başka bir program tarafından kullanılıyor. "
                              "Ayarlar'dan farklı bir tuş seç.")
            return False
        self._registered = True
        self.current = text
        return True

    def unregister(self) -> None:
        if self._registered:
            try:
                self._u32().UnregisterHotKey(None, self._id)
            except Exception:
                log.debug("UnregisterHotKey başarısız", exc_info=True)
            self._registered = False
