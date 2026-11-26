"""Windows Hello / PIN / parola doğrulaması (UserConsentVerifier). Bloklar: worker thread'de çağır.

Dönüş: "ok" | "denied" | "unavailable" (Hello/PIN kurulu değil ya da bileşen yok).
"""
from __future__ import annotations

import asyncio
import logging
import sys

log = logging.getLogger(__name__)


async def _ask(message: str) -> str:
    from winrt.windows.security.credentials.ui import (UserConsentVerificationResult, UserConsentVerifier,
                                                       UserConsentVerifierAvailability)

    if await UserConsentVerifier.check_availability_async() != UserConsentVerifierAvailability.AVAILABLE:
        return "unavailable"
    res = await UserConsentVerifier.request_verification_async(message)
    return "ok" if res == UserConsentVerificationResult.VERIFIED else "denied"


def available() -> bool:
    """İstem göstermeden yalnızca Hello/PIN'in kullanılabilir olup olmadığına bakar."""
    if sys.platform != "win32":
        return False
    try:
        from winrt.windows.security.credentials.ui import UserConsentVerifier, UserConsentVerifierAvailability

        async def chk():
            return await UserConsentVerifier.check_availability_async() == UserConsentVerifierAvailability.AVAILABLE

        return bool(asyncio.run(chk()))
    except Exception:
        log.info("Windows Hello denetlenemedi", exc_info=True)
        return False


def verify(message: str) -> str:
    if sys.platform != "win32":
        return "unavailable"
    try:
        return asyncio.run(_ask(message))
    except Exception:
        log.warning("Windows doğrulaması çalışmadı", exc_info=True)
        return "unavailable"


# ---------------------------------------------------------------- Hello yoksa: Windows parola penceresi
def verify_password(tries: int = 3) -> str:
    """Windows'un kendi kimlik doğrulama penceresini açar, girilen parolayı LogonUser ile doğrular.
    Parola Reis'te saklanmaz, loglanmaz; kullanıldıktan sonra bellekte sıfırlanır. "ok" | "denied"."""
    import ctypes
    from ctypes import byref, c_void_p, create_unicode_buffer, sizeof, wintypes

    class CREDUI_INFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hwndParent", wintypes.HWND), ("pszMessageText", wintypes.LPCWSTR),
                    ("pszCaptionText", wintypes.LPCWSTR), ("hbmBanner", wintypes.HANDLE)]

    credui, adv, ole32, k32 = (ctypes.WinDLL(n) for n in ("credui", "advapi32", "ole32", "kernel32"))
    credui.CredUIPromptForWindowsCredentialsW.argtypes = [
        c_void_p, wintypes.DWORD, c_void_p, c_void_p, wintypes.ULONG, c_void_p, c_void_p, c_void_p, wintypes.DWORD]
    credui.CredUnPackAuthenticationBufferW.argtypes = [
        wintypes.DWORD, c_void_p, wintypes.DWORD, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p]
    adv.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               c_void_p]
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    info = CREDUI_INFOW(sizeof(CREDUI_INFOW), None, "Şifre listesini açmak için Windows parolanı gir.", "Reis", None)
    err = 0
    for _ in range(max(1, tries)):
        pkg, out_size, save = wintypes.ULONG(0), wintypes.ULONG(0), wintypes.BOOL(False)
        out_buf = c_void_p()
        rc = credui.CredUIPromptForWindowsCredentialsW(byref(info), err, byref(pkg), None, 0, byref(out_buf),
                                                       byref(out_size), byref(save), 0x200)  # yalnızca mevcut kullanıcı
        if rc != 0:
            return "denied"  # iptal (1223) ya da hata
        user, dom, pw = (create_unicode_buffer(512) for _ in range(3))
        ul, dl, pl = wintypes.DWORD(512), wintypes.DWORD(512), wintypes.DWORD(512)
        ok = credui.CredUnPackAuthenticationBufferW(0, out_buf, out_size.value, user, byref(ul), dom, byref(dl),
                                                    pw, byref(pl))
        ctypes.memset(out_buf, 0, out_size.value)
        ole32.CoTaskMemFree(out_buf)
        passed = False
        if ok:
            u, d = user.value, dom.value
            if not d and "\\" in u:
                d, u = u.split("\\", 1)
            elif not d and "@" not in u:
                d = "."  # yerel hesap
            token = wintypes.HANDLE()
            passed = bool(adv.LogonUserW(u, d or None, pw, 2, 0, byref(token)))  # LOGON32_LOGON_INTERACTIVE
            if passed:
                k32.CloseHandle(token)
        ctypes.memset(pw, 0, sizeof(pw))
        if passed:
            return "ok"
        err = 1326  # ERROR_LOGON_FAILURE: pencere "parola yanlış" der
    return "denied"


def authenticate(message: str) -> str:
    """Önce Windows Hello/PIN; yoksa Windows parola penceresi. "ok" | "denied" | "unavailable"."""
    r = verify(message)
    if r != "unavailable":
        return r
    if sys.platform != "win32":
        return "unavailable"
    try:
        return verify_password()
    except Exception:
        log.warning("Windows parola doğrulaması çalışmadı", exc_info=True)
        return "unavailable"
