"""Windows DPAPI (CryptProtectData): veriyi bu Windows kullanıcı hesabına bağlayarak şifreler.

Dosya yalnızca aynı bilgisayarda, aynı Windows kullanıcısı tarafından çözülebilir; ayrıca parola gerekmez.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, Structure, byref, c_byte, c_void_p, create_string_buffer, string_at, wintypes

ENTROPY = b"Reis.passwords.v1"  # uygulamaya özgü ek girdi


class DpapiError(Exception):
    pass


class _Blob(Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_byte))]


def _in(data: bytes):
    buf = create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, POINTER(c_byte))), buf  # buf canlı kalmalı


def _run(fn_name: str, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise DpapiError("DPAPI yalnızca Windows'ta var.")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [c_void_p]
    inb, keep1 = _in(data)
    ent, keep2 = _in(ENTROPY)
    out = _Blob()
    fn = getattr(crypt32, fn_name)
    if fn_name == "CryptProtectData":
        ok = fn(byref(inb), "Reis", byref(ent), None, None, 0, byref(out))
    else:
        ok = fn(byref(inb), None, byref(ent), None, None, 0, byref(out))
    del keep1, keep2
    if not ok:
        raise DpapiError("Windows şifreleme işlemi başarısız (bu hesapla oluşturulmamış olabilir).")
    try:
        return string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _run("CryptProtectData", data)


def unprotect(blob: bytes) -> bytes:
    return _run("CryptUnprotectData", blob)
